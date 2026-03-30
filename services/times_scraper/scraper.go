package main

import (
	"bytes"
	"encoding/json"
	"fmt"
	"os/exec"
	"regexp"
	"strconv"
	"strings"

	"github.com/PuerkitoBio/goquery"
)

type Clue struct {
	Number      int    `json:"number"`
	Text        string `json:"text"`
	LetterCount string `json:"letter_count"`
	Answer      string `json:"answer"`
	Explanation string `json:"explanation"`
}

type Crossword struct {
	PuzzleNumber int    `json:"puzzle_number"`
	Date         string `json:"date"`
	Blogger      string `json:"blogger"`
	URL          string `json:"url"`
	Across       []Clue `json:"across"`
	Down         []Clue `json:"down"`
}

var (
	// Matches: "Times 29503", "Times Cryptic 29496", "Times Cryptic No. 27282"
	puzzleNumRe   = regexp.MustCompile(`(?i)Times\s+(?:Cryptic\s+)?(?:No\.?\s+)?(\d{4,5})`)
	letterCountRe = regexp.MustCompile(`\([\d,]+\)\s*$`)
	clueNumRe     = regexp.MustCompile(`^(\d+)\s+`)
)

func urlSlug(url string) string {
	return url[strings.LastIndex(url, "/")+1:]
}

func isSaturdayURL(url string) bool {
	return strings.HasPrefix(urlSlug(url), "saturday-")
}

// fetchSaturdayHTML fetches via the WP REST API (saturday- slugs are blocked by Mod_Security
// on the normal frontend) and returns the rendered post HTML.
func fetchSaturdayHTML(url string) ([]byte, string, string, error) {
	slug := urlSlug(url)
	apiURL := "https://timesforthetimes.co.uk/wp-json/wp/v2/posts?slug=" + slug
	out, err := exec.Command("curl", "-s", apiURL).Output()
	if err != nil {
		return nil, "", "", fmt.Errorf("curl REST API %s: %w", apiURL, err)
	}
	var posts []struct {
		Date    string `json:"date"`
		Title   struct{ Rendered string `json:"rendered"` } `json:"title"`
		Content struct{ Rendered string `json:"rendered"` } `json:"content"`
	}
	if err := json.Unmarshal(out, &posts); err != nil || len(posts) == 0 {
		return nil, "", "", fmt.Errorf("no post found at REST API for slug %s", slug)
	}
	return []byte(posts[0].Content.Rendered), posts[0].Title.Rendered, posts[0].Date, nil
}

func fetch(url string) ([]byte, error) {
	out, err := exec.Command("curl", "-s", "-L", "--compressed",
		"-A", "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
		"-H", "Accept: text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
		"-H", "Accept-Language: en-GB,en;q=0.9",
		url,
	).Output()
	if err != nil {
		return nil, fmt.Errorf("curl %s: %w", url, err)
	}
	return out, nil
}

func parseClueRow(clueRow, ansRow *goquery.Selection) (Clue, bool) {
	tds := clueRow.Find("td")
	numStr := strings.TrimSpace(tds.First().Text())
	num, err := strconv.Atoi(numStr)
	if err != nil {
		return Clue{}, false
	}

	clueText := strings.TrimSpace(tds.Last().Text())
	letterCount := ""
	if m := letterCountRe.FindString(clueText); m != "" {
		letterCount = strings.Trim(strings.TrimSpace(m), "()")
		clueText = strings.TrimSpace(letterCountRe.ReplaceAllString(clueText, ""))
	}

	ansCell := ansRow.Find("td").Last()
	answer := strings.TrimSpace(ansCell.Find("strong, b").First().Text())

	fullAns := strings.TrimSpace(ansCell.Text())
	explanation := ""
	if idx := strings.Index(fullAns, answer); idx != -1 {
		rest := strings.TrimLeft(fullAns[idx+len(answer):], " \u2013\u2014-")
		explanation = strings.TrimSpace(rest)
	}

	return Clue{
		Number:      num,
		Text:        clueText,
		LetterCount: letterCount,
		Answer:      answer,
		Explanation: explanation,
	}, true
}

// parseNewFormatClues handles <table class="clues"> tbody rows (no direction header in tbody).
func parseNewFormatClues(rows *goquery.Selection) []Clue {
	var clues []Clue
	n := rows.Length()
	for i := 0; i < n-1; i += 2 {
		if c, ok := parseClueRow(rows.Eq(i), rows.Eq(i+1)); ok {
			clues = append(clues, c)
		}
	}
	return clues
}

// parseOldFormatTable handles <table cellspacing="3"> where Across and Down may both
// appear in a single table separated by plain-text direction header rows.
func parseOldFormatTable(rows *goquery.Selection) (across, down []Clue) {
	var current *[]Clue
	i := 0
	n := rows.Length()
	for i < n {
		row := rows.Eq(i)
		firstText := strings.TrimSpace(row.Find("td").First().Text())
		switch strings.ToLower(firstText) {
		case "across":
			current = &across
			i++
		case "down":
			current = &down
			i++
		default:
			if current == nil || firstText == "" || i+1 >= n {
				i++
				continue
			}
			if c, ok := parseClueRow(rows.Eq(i), rows.Eq(i+1)); ok {
				*current = append(*current, c)
				i += 2
			} else {
				i++
			}
		}
	}
	return
}

// parseProse handles the Saturday post format where clues are in <p> tags:
//   <span style="color:blue;">[<span>]N clue text (count)[</span>]</span><br/><b>ANSWER:</b> explanation
// Direction sections are marked by <b><span style="font-size:18.0pt;">Across/Down</span></b>.
func parseProse(doc *goquery.Document) (across, down []Clue) {
	var current *[]Clue

	doc.Find("p").Each(func(_ int, p *goquery.Selection) {
		// Direction header: bold span with large font — may share <p> with first clue
		// Check for blue clue span first; only look for header if not already in a clue.
		blueSpan := p.Find(`span[style*="color:blue"], span[style*="color: blue"]`).First()
		if blueSpan.Length() == 0 {
			header := strings.TrimSpace(p.Find("b span").First().Text())
			switch strings.ToLower(header) {
			case "across":
				current = &across
			case "down":
				current = &down
			}
			return
		}

		// Direction header may share its <p> with the first clue — detect and set direction.
		header := strings.TrimSpace(p.Find("b span").First().Text())
		switch strings.ToLower(header) {
		case "across":
			current = &across
		case "down":
			current = &down
		}

		if current == nil {
			return
		}
		clueRaw := strings.TrimSpace(blueSpan.Text())
		m := clueNumRe.FindStringSubmatch(clueRaw)
		if m == nil {
			return
		}
		num, _ := strconv.Atoi(m[1])
		clueText := strings.TrimSpace(clueRaw[len(m[0]):])

		letterCount := ""
		if lm := letterCountRe.FindString(clueText); lm != "" {
			letterCount = strings.Trim(strings.TrimSpace(lm), "()")
			clueText = strings.TrimSpace(letterCountRe.ReplaceAllString(clueText, ""))
		}

		// Answer is in the first <b> tag; format is "ANSWER:" (colon after answer)
		boldText := strings.TrimSpace(p.Find("b").First().Text())
		answer := strings.TrimSuffix(boldText, ":")

		// Explanation is the paragraph text after the bold answer
		fullText := strings.TrimSpace(p.Text())
		explanation := ""
		if idx := strings.Index(fullText, boldText); idx != -1 {
			rest := strings.TrimLeft(fullText[idx+len(boldText):], " ")
			explanation = strings.TrimSpace(rest)
		}

		*current = append(*current, Clue{
			Number:      num,
			Text:        clueText,
			LetterCount: letterCount,
			Answer:      answer,
			Explanation: explanation,
		})
	})
	return
}

func Scrape(url string) (*Crossword, error) {
	xwd := &Crossword{URL: url}

	// Saturday posts are blocked by Mod_Security on the frontend; use WP REST API instead.
	if isSaturdayURL(url) {
		html, title, date, err := fetchSaturdayHTML(url)
		if err != nil {
			return nil, err
		}
		if m := puzzleNumRe.FindStringSubmatch(title); m != nil {
			xwd.PuzzleNumber, _ = strconv.Atoi(m[1])
		}
		xwd.Date = date
		doc, err := goquery.NewDocumentFromReader(bytes.NewReader(html))
		if err != nil {
			return nil, fmt.Errorf("parse prose %s: %w", url, err)
		}
		xwd.Across, xwd.Down = parseProse(doc)
		return xwd, nil
	}

	body, err := fetch(url)
	if err != nil {
		return nil, err
	}

	doc, err := goquery.NewDocumentFromReader(bytes.NewReader(body))
	if err != nil {
		return nil, fmt.Errorf("parse %s: %w", url, err)
	}

	title := strings.TrimSpace(doc.Find("h1.entry-title").First().Text())
	if m := puzzleNumRe.FindStringSubmatch(title); m != nil {
		xwd.PuzzleNumber, _ = strconv.Atoi(m[1])
	}
	xwd.Date = strings.TrimSpace(doc.Find("time.entry-date.published").First().Text())
	xwd.Blogger = strings.TrimSpace(doc.Find(".byline a.url").First().Text())

	// New format: <table class="clues"> with <thead> direction and td.num / td.clue / td.ans
	doc.Find("table.clues").Each(func(_ int, table *goquery.Selection) {
		direction := strings.ToLower(strings.TrimSpace(table.Find("thead th").First().Text()))
		clues := parseNewFormatClues(table.Find("tbody tr"))
		switch direction {
		case "across":
			xwd.Across = clues
		case "down":
			xwd.Down = clues
		}
	})

	// Old format: <table cellspacing="3"> — may be one table per direction or a single
	// table with both, separated by plain-text "Across"/"Down" header rows.
	if len(xwd.Across) == 0 && len(xwd.Down) == 0 {
		doc.Find("table[cellspacing='3']").Each(func(_ int, table *goquery.Selection) {
			ac, dn := parseOldFormatTable(table.Find("tbody tr"))
			if xwd.Across == nil {
				xwd.Across = ac
			}
			if xwd.Down == nil {
				xwd.Down = dn
			}
		})
	}

	return xwd, nil
}
