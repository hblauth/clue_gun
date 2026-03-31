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
	// Matches: "Times 29503", "Times Cryptic 29496", "Times Cryptic No. 27282", "Times 23,034"
	puzzleNumRe   = regexp.MustCompile(`(?i)Times\s+(?:Cryptic\s+)?(?:No\.?\s+)?(\d{1,2},\d{3}|\d{4,5})`)
	letterCountRe = regexp.MustCompile(`\([\d,]+\)\s*$`)
	clueNumRe     = regexp.MustCompile(`^(\d+)\s+`)

	// Separates the answer from the explanation in embedded-answer rows.
	// Handles: " – "/" — ", " = ", ".  " (period+2+spaces), ". X" (period+capital),
	// ", x" (comma+space+lowercase — blogger continuing with explanation)
	embeddedSepRe = regexp.MustCompile(`\s+[–—]\s+|\s+=\s+|\.{1,2}\s{2,}|\.\s+[A-Z(]|,\s+[a-z]`)

	// Strips blogger wordplay notation from a raw answer token, leaving only letters.
	// Removes lowercase-only parenthetical hints: (lish), (l), etc.
	lowerParenRe = regexp.MustCompile(`\([^A-Z)]*\)`)
	// Collapses uppercase parenthetical content: (ARDO) → ARDO, (A,L) → AL
	upperParenRe = regexp.MustCompile(`\(([A-Z,]+)\)`)
	// Removes remaining construction notation: commas, *, spaces around +
	notationRe = regexp.MustCompile(`[,*]|\s*\+\s*`)
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

// parseOldFormatTable handles old-format <table cellspacing="3|5"> tables.
// It detects three row patterns automatically:
//
//	Pattern 1 (original):  [num td][clue text td] / [bold answer td][explanation td]
//	Pattern A (embedded):  [num td][ANSWER. explanation td]  — answer in caps, no bold
//	Pattern B (single-cell): [num<i>clue</i>] / [<strong>ANSWER</strong> explanation]
func parseOldFormatTable(rows *goquery.Selection) (across, down []Clue) {
	var current *[]Clue
	i := 0
	n := rows.Length()
	for i < n {
		row := rows.Eq(i)

		// Direction header: check all cell text, bold text, and th text.
		rowText := strings.ToLower(strings.TrimSpace(row.Text()))
		boldText := strings.ToLower(strings.TrimSpace(row.Find("strong,b").First().Text()))
		switch {
		case rowText == "across" || boldText == "across":
			current = &across
			i++
			continue
		case rowText == "down" || boldText == "down":
			current = &down
			i++
			continue
		}

		tds := row.Find("td")
		// If no direction header has appeared yet, default to across so that
		// tables without headers (bloggers who omit "Across"/"Down") still parse.
		if current == nil && tds.Length() > 0 {
			current = &across
		}
		if current == nil {
			i++
			continue
		}
		numCells := tds.Length()

		switch {
		case numCells == 0:
			i++

		case numCells == 1:
			// Pattern B: single-cell rows alternating clue / answer.
			// A clue row starts with a digit; an answer row starts with bold.
			cell := tds.First()
			if cell.Find("strong,b").Length() > 0 {
				// Stray answer row with no preceding clue row — skip.
				i++
				continue
			}
			numStr := strings.TrimSpace(clueNumRe.FindString(cell.Text()))
			if numStr == "" {
				i++
				continue
			}
			if i+1 >= n {
				i++
				continue
			}
			if c, ok := parseSingleCellClueRow(cell, rows.Eq(i+1)); ok {
				*current = append(*current, c)
				i += 2
			} else {
				i++
			}

		default:
			// 2+ cells. Determine whether the answer is in the next row (Pattern 1)
			// or embedded in this row's second cell (Pattern A).
			numStr := strings.TrimSpace(tds.First().Text())
			num, err := strconv.Atoi(numStr)
			if err != nil {
				i++
				continue
			}

			// Look ahead: if the next row's first cell is NOT a plain number and
			// it contains a bold tag, it's a Pattern 1 answer row.
			isPattern1 := false
			if i+1 < n {
				nextRow := rows.Eq(i + 1)
				nextFirstText := strings.TrimSpace(nextRow.Find("td,th").First().Text())
				_, nextNumErr := strconv.Atoi(nextFirstText)
				if nextNumErr != nil && nextRow.Find("strong,b").Length() > 0 {
					isPattern1 = true
				}
			}

			if isPattern1 {
				if c, ok := parseClueRow(rows.Eq(i), rows.Eq(i+1)); ok {
					*current = append(*current, c)
					i += 2
				} else {
					i++
				}
			} else {
				// Pattern A: answer embedded in second cell.
				cellText := strings.TrimSpace(tds.Last().Text())
				if c, ok := parseEmbeddedClueRow(num, cellText); ok {
					*current = append(*current, c)
				}
				i++
			}
		}
	}
	return
}

// parseSingleCellClueRow handles Pattern B: a single <td> containing "N<i>clue (count)</i>"
// followed by a single-cell answer row with a <strong> tag.
func parseSingleCellClueRow(clueCell, ansRow *goquery.Selection) (Clue, bool) {
	raw := strings.TrimSpace(clueCell.Text())
	m := clueNumRe.FindStringSubmatch(raw)
	if m == nil {
		return Clue{}, false
	}
	num, _ := strconv.Atoi(strings.TrimSpace(m[1]))

	clueText := strings.TrimSpace(raw[len(m[0]):])
	letterCount := ""
	if lm := letterCountRe.FindString(clueText); lm != "" {
		letterCount = strings.Trim(strings.TrimSpace(lm), "()")
		clueText = strings.TrimSpace(letterCountRe.ReplaceAllString(clueText, ""))
	}

	ansCell := ansRow.Find("td").First()
	answer := strings.TrimSpace(ansCell.Find("strong,b").First().Text())
	if answer == "" {
		return Clue{}, false
	}
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

// parseEmbeddedClueRow handles Pattern A: the answer and explanation are in a single
// cell, with the answer as leading all-caps text separated by ". ", " – ", " = ", etc.
// No clue text is available in this format (bloggers don't repeat the clue).
func parseEmbeddedClueRow(num int, cellText string) (Clue, bool) {
	if cellText == "" {
		return Clue{}, false
	}
	loc := embeddedSepRe.FindStringIndex(cellText)
	var rawAnswer, explanation string
	if loc != nil {
		rawAnswer = strings.TrimSpace(cellText[:loc[0]])
		// If the separator is ". X" we've consumed one char of the explanation.
		sep := cellText[loc[0]:loc[1]]
		if strings.HasPrefix(strings.TrimLeft(sep, " ."), "") && loc[1] > 0 {
			lastChar := cellText[loc[1]-1]
			if lastChar >= 'A' && lastChar <= 'Z' || lastChar == '(' {
				explanation = strings.TrimSpace(cellText[loc[1]-1:])
			} else {
				explanation = strings.TrimSpace(cellText[loc[1]:])
			}
		}
	} else {
		rawAnswer = strings.TrimSpace(cellText)
	}

	answer := cleanEmbeddedAnswer(rawAnswer)
	if answer == "" {
		return Clue{}, false
	}
	return Clue{
		Number:      num,
		Answer:      answer,
		Explanation: explanation,
	}, true
}

// cleanEmbeddedAnswer strips blogger wordplay notation from a raw answer token,
// returning only the uppercase letters of the actual answer.
//
//	"CHAR,LOCK"      → "CHARLOCK"
//	"L(ARDO)ON"      → "LARDOON"   (uppercase parens kept, lowercase discarded)
//	"TH(RIFT)Y"      → "THRIFTY"
//	"H + AVE"        → "HAVE"
//	"FIRST STAGE"    → "FIRST STAGE"  (multi-word answers preserved)
func cleanEmbeddedAnswer(raw string) string {
	// Remove lowercase-only parentheticals: (lish), (l)
	s := lowerParenRe.ReplaceAllString(raw, "")
	// Collapse uppercase parentheticals: (ARDO) → ARDO, (A,L) → AL
	s = upperParenRe.ReplaceAllStringFunc(s, func(m string) string {
		return notationRe.ReplaceAllString(m[1:len(m)-1], "")
	})
	// Strip commas, *, and + with surrounding spaces
	s = notationRe.ReplaceAllString(s, "")
	s = strings.TrimSpace(s)
	// Must start with an uppercase letter to be a valid answer.
	if len(s) == 0 || s[0] < 'A' || s[0] > 'Z' {
		return ""
	}
	return s
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

// ScrapeBytes parses a crossword from pre-fetched HTML bytes.
// Use this when reading from a local HTML cache.
func ScrapeBytes(url string, body []byte) (*Crossword, error) {
	doc, err := goquery.NewDocumentFromReader(bytes.NewReader(body))
	if err != nil {
		return nil, fmt.Errorf("parse %s: %w", url, err)
	}
	return parseDoc(url, doc), nil
}

func Scrape(url string) (*Crossword, error) {
	// Saturday posts are blocked by Mod_Security on the frontend; use WP REST API instead.
	if isSaturdayURL(url) {
		html, title, date, err := fetchSaturdayHTML(url)
		if err != nil {
			return nil, err
		}
		xwd := &Crossword{URL: url, Date: date}
		if m := puzzleNumRe.FindStringSubmatch(title); m != nil {
			xwd.PuzzleNumber, _ = strconv.Atoi(strings.ReplaceAll(m[1], ",", ""))
		}
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
	return ScrapeBytes(url, body)
}

// FetchHTML downloads the raw HTML for a URL. For Saturday posts this reconstructs
// a minimal HTML document from the WP REST API content fragment so the cache
// format is consistent (always a complete HTML doc).
func FetchHTML(url string) ([]byte, error) {
	if isSaturdayURL(url) {
		content, title, date, err := fetchSaturdayHTML(url)
		if err != nil {
			return nil, err
		}
		// Wrap fragment in a minimal HTML shell so parseDoc works on cached files.
		wrapped := fmt.Sprintf(
			`<html><head><title>%s</title><meta name="date" content="%s"></head><body><article><h1 class="entry-title">%s</h1>%s</article></body></html>`,
			title, date, title, string(content),
		)
		return []byte(wrapped), nil
	}
	return fetch(url)
}

func parseDoc(url string, doc *goquery.Document) *Crossword {
	xwd := &Crossword{URL: url}

	title := strings.TrimSpace(doc.Find("h1.entry-title").First().Text())
	if m := puzzleNumRe.FindStringSubmatch(title); m != nil {
		xwd.PuzzleNumber, _ = strconv.Atoi(strings.ReplaceAll(m[1], ",", ""))
	}
	xwd.Date = strings.TrimSpace(doc.Find("time.entry-date.published").First().Text())
	if xwd.Date == "" {
		// Saturday cached pages store date in a meta tag.
		xwd.Date = strings.TrimSpace(doc.Find(`meta[name="date"]`).AttrOr("content", ""))
	}
	xwd.Blogger = strings.TrimSpace(doc.Find(".byline a.url").First().Text())

	// Saturday prose format.
	if isSaturdayURL(url) {
		xwd.Across, xwd.Down = parseProse(doc)
		return xwd
	}

	// New format: <table class="clues">
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

	// Old format: <table cellspacing="3"> or <table cellspacing="5">
	if len(xwd.Across) == 0 && len(xwd.Down) == 0 {
		doc.Find("table[cellspacing='3'],table[cellspacing='5']").Each(func(_ int, table *goquery.Selection) {
			ac, dn := parseOldFormatTable(table.Find("tr"))
			// If both directions came back empty for down (no direction headers in table),
			// assign across clues to whichever direction is still missing.
			if len(xwd.Across) == 0 {
				xwd.Across = ac
				xwd.Down = dn
			} else if len(xwd.Down) == 0 {
				// Second table: treat its across result as down if it has no header.
				if len(dn) == 0 && len(ac) > 0 {
					xwd.Down = ac
				} else {
					xwd.Down = dn
				}
			}
		})
	}

	return xwd
}
