package main

import (
	"bufio"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"time"

	"github.com/spf13/cobra"
)

func main() {
	rootCmd := &cobra.Command{
		Use:   "times-scraper",
		Short: "Scrape Times crosswords from timesforthetimes.co.uk",
	}

	// Single URL → stdout
	scrapeCmd := &cobra.Command{
		Use:   "scrape <url>",
		Short: "Scrape a single puzzle and print JSON to stdout",
		Args:  cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			xwd, err := Scrape(args[0])
			if err != nil {
				return err
			}
			enc := json.NewEncoder(os.Stdout)
			enc.SetIndent("", "  ")
			return enc.Encode(xwd)
		},
	}

	// Batch mode: read URLs from file, save JSON per puzzle
	var outDir string
	var delayMs int
	var htmlCacheDir string

	batchCmd := &cobra.Command{
		Use:   "batch <urls-file>",
		Short: "Scrape all URLs in a file, saving one JSON per puzzle",
		Args:  cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			return runBatch(args[0], outDir, htmlCacheDir, time.Duration(delayMs)*time.Millisecond)
		},
	}
	batchCmd.Flags().StringVarP(&outDir, "out", "o", "../../data/puzzles", "Output directory for JSON files")
	batchCmd.Flags().IntVarP(&delayMs, "delay", "d", 2000, "Delay between requests in milliseconds")
	batchCmd.Flags().StringVar(&htmlCacheDir, "html-cache", "", "Directory of cached HTML files; fetch from network if not found")

	// fetch-html: download raw HTML for each URL without parsing
	var fetchDelay int

	fetchHTMLCmd := &cobra.Command{
		Use:   "fetch-html <urls-file> <cache-dir>",
		Short: "Download raw HTML for each URL and save to cache-dir/{slug}.html",
		Args:  cobra.ExactArgs(2),
		RunE: func(cmd *cobra.Command, args []string) error {
			return runFetchHTML(args[0], args[1], time.Duration(fetchDelay)*time.Millisecond)
		},
	}
	fetchHTMLCmd.Flags().IntVarP(&fetchDelay, "delay", "d", 2000, "Delay between requests in milliseconds")

	rootCmd.AddCommand(scrapeCmd, batchCmd, fetchHTMLCmd)

	if err := rootCmd.Execute(); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}

func runBatch(urlsFile, outDir, htmlCacheDir string, delay time.Duration) error {
	if err := os.MkdirAll(outDir, 0755); err != nil {
		return fmt.Errorf("create output dir: %w", err)
	}

	urls, err := readLines(urlsFile)
	if err != nil {
		return err
	}

	total := len(urls)
	skipped, done, failed := 0, 0, 0

	for i, url := range urls {
		puzzleNum := extractPuzzleNum(url)
		outPath := filepath.Join(outDir, puzzleNum+".json")

		if _, err := os.Stat(outPath); err == nil {
			skipped++
			continue
		}

		fmt.Fprintf(os.Stderr, "[%d/%d] %s ... ", i+1, total, url)

		var xwd *Crossword
		if htmlCacheDir != "" {
			cachePath := filepath.Join(htmlCacheDir, urlSlug(url)+".html")
			if body, err := os.ReadFile(cachePath); err == nil {
				xwd, err = ScrapeBytes(url, body)
				if err != nil {
					fmt.Fprintf(os.Stderr, "FAILED parse cached: %v\n", err)
					failed++
					continue
				}
			}
		}

		if xwd == nil {
			xwd, err = Scrape(url)
			if err != nil {
				fmt.Fprintf(os.Stderr, "FAILED: %v\n", err)
				failed++
				continue
			}
			time.Sleep(delay)
		}

		if xwd.PuzzleNumber == 0 || (len(xwd.Across) == 0 && len(xwd.Down) == 0) {
			fmt.Fprintf(os.Stderr, "EMPTY (no clues parsed)\n")
			failed++
			continue
		}

		data, err := json.MarshalIndent(xwd, "", "  ")
		if err != nil {
			fmt.Fprintf(os.Stderr, "FAILED marshal: %v\n", err)
			failed++
			continue
		}
		data = append(data, '\n')
		if err := os.WriteFile(outPath, data, 0644); err != nil {
			fmt.Fprintf(os.Stderr, "FAILED write: %v\n", err)
			failed++
			continue
		}

		done++
		fmt.Fprintf(os.Stderr, "OK (#%d, %dac %ddn)\n",
			xwd.PuzzleNumber, len(xwd.Across), len(xwd.Down))
	}

	fmt.Fprintf(os.Stderr, "\nDone. scraped=%d  skipped=%d  failed=%d  total=%d\n",
		done, skipped, failed, total)
	return nil
}

func runFetchHTML(urlsFile, cacheDir string, delay time.Duration) error {
	if err := os.MkdirAll(cacheDir, 0755); err != nil {
		return fmt.Errorf("create cache dir: %w", err)
	}

	urls, err := readLines(urlsFile)
	if err != nil {
		return err
	}

	total := len(urls)
	skipped, done, failed := 0, 0, 0

	for i, url := range urls {
		cachePath := filepath.Join(cacheDir, urlSlug(url)+".html")

		if _, err := os.Stat(cachePath); err == nil {
			skipped++
			continue
		}

		fmt.Fprintf(os.Stderr, "[%d/%d] %s ... ", i+1, total, url)

		body, err := FetchHTML(url)
		if err != nil {
			fmt.Fprintf(os.Stderr, "FAILED: %v\n", err)
			failed++
			continue
		}

		if err := os.WriteFile(cachePath, body, 0644); err != nil {
			fmt.Fprintf(os.Stderr, "FAILED write: %v\n", err)
			failed++
			continue
		}

		done++
		fmt.Fprintf(os.Stderr, "OK (%d bytes)\n", len(body))
		time.Sleep(delay)
	}

	fmt.Fprintf(os.Stderr, "\nDone. fetched=%d  skipped=%d  failed=%d  total=%d\n",
		done, skipped, failed, total)
	return nil
}

func readLines(path string) ([]string, error) {
	f, err := os.Open(path)
	if err != nil {
		return nil, fmt.Errorf("open %s: %w", path, err)
	}
	defer f.Close()

	var lines []string
	scanner := bufio.NewScanner(f)
	for scanner.Scan() {
		if u := strings.TrimSpace(scanner.Text()); u != "" {
			lines = append(lines, u)
		}
	}
	return lines, scanner.Err()
}

func extractPuzzleNum(url string) string {
	slug := urlSlug(url)
	var best4 string
	for _, part := range strings.Split(slug, "-") {
		if _, err := strconv.Atoi(part); err != nil {
			continue
		}
		if len(part) == 5 {
			return part
		}
		if len(part) == 4 && best4 == "" {
			best4 = part
		}
	}
	if best4 != "" {
		return best4
	}
	return slug
}
