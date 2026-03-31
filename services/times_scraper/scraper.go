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
	// Also handles: "Times – 23945" (en dash separator), "Times 26;971" (semicolon typo),
	// and 6-digit special numbers like "Times 120430".
	puzzleNumRe = regexp.MustCompile(`(?i)Times\s+(?:Cryptic\s+)?(?:No\.?\s+)?[–—\s]*(\d{1,2}[,;]\d{3}|\d{4,6})`)
	letterCountRe = regexp.MustCompile(`\([\d,]+\)\s*$`)
	clueNumRe     = regexp.MustCompile(`^(\d+)[\s\x{00a0}]+`)

	// boldTagRe extracts the text inside the first <b> or <strong> tag.
	boldTagRe = regexp.MustCompile(`(?i)<(?:b|strong)[^>]*>([^<]*)</(?:b|strong)>`)
	// htmlTagRe strips all HTML tags from a fragment.
	htmlTagRe = regexp.MustCompile(`<[^>]+>`)

	// Separates the answer from the explanation in embedded-answer rows.
	// Handles: " – "/" — ", " = ", ".  " (period+2+spaces), ". X" (period+capital),
	// ", x" (comma+space+lowercase — blogger continuing with explanation)
	embeddedSepRe = regexp.MustCompile(`\s+[–—]\s+|\s+=\s+|\.{1,2}\s{2,}|\.\s+[A-Z(]|,\s+[a-z]`)

	// proseSepRe extends embeddedSepRe for plain-prose clue lines where 3+ spaces
	// or a tab character separates the answer notation from the explanation.
	proseSepRe = regexp.MustCompile(`\s+[–—]\s+|\s+=\s+|\.{1,2}\s{2,}|\.\s+[A-Z(]|,\s+[a-z]|\t|\s{3,}`)

	// Strips blogger wordplay notation from a raw answer token, leaving only letters.
	// Removes parentheticals containing at least one lowercase letter: (l), (lish),
	// (Sub reversed), (deacon is)*, etc. Superset of the old lowerParenRe.
	mixedParenRe = regexp.MustCompile(`\([^)]*[a-z][^)]*\)`)
	// Collapses uppercase parenthetical content: (ARDO) → ARDO, (A,L) → AL
	upperParenRe = regexp.MustCompile(`\(([A-Z,]+)\)`)
	// Removes remaining construction notation: commas, *, spaces around +
	notationRe = regexp.MustCompile(`[,*]|\s*\+\s*`)
)

func urlSlug(url string) string {
	return url[strings.LastIndex(url, "/")+1:]
}

// puzzleNumFromURL extracts a 4–6 digit puzzle number from the URL slug as a fallback
// when the post title doesn't match the standard "Times NNNNN" pattern.
func puzzleNumFromURL(url string) int {
	slug := urlSlug(url)
	for _, part := range strings.Split(slug, "-") {
		if len(part) >= 4 && len(part) <= 6 {
			if n, err := strconv.Atoi(part); err == nil {
				return n
			}
		}
	}
	return 0
}

func isSaturdayURL(url string) bool {
	// Only older-style slugs like "saturday-30-may-..." use the WP REST API prose format.
	// Newer slugs like "times-cryptic-no-28008-saturday-19-june-..." use the table format
	// and are fetched as regular pages.
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
	// Remove parentheticals containing any lowercase letter: (lish), (l), (Sub reversed)
	s := mixedParenRe.ReplaceAllString(raw, "")
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

// parseDivProse handles blog posts where each clue and its answer occupy
// separate <div> blocks in an alternating pattern:
//
//	<div>N clue text (letter_count)</div>
//	<div>[<b>]ANSWER[</b>] explanation</div>
//
// Direction headers are standalone divs containing "Across" or "Down".
// When no headers are present, direction is inferred from clue-number resets.
func parseDivProse(doc *goquery.Document) (across, down []Clue) {
	var current *[]Clue
	var pending *Clue
	lastNum := 0

	doc.Find(".entry-content div").Each(func(_ int, div *goquery.Selection) {
		// Skip container divs that hold child divs.
		if div.Find("div").Length() > 0 {
			return
		}
		text := strings.TrimSpace(div.Text())
		if text == "" {
			return
		}

		lower := strings.ToLower(text)
		if lower == "across" {
			current = &across
			pending = nil
			lastNum = 0
			return
		}
		if lower == "down" {
			current = &down
			pending = nil
			lastNum = 0
			return
		}

		// Clue div: starts with a clue number.
		m := clueNumRe.FindStringSubmatch(text)
		if m != nil {
			num, _ := strconv.Atoi(strings.TrimSpace(m[1]))
			if current == nil {
				current = &across
			} else if num <= lastNum && current == &across && len(across) > 0 {
				// Number reset → switch to Down (no explicit header present).
				current = &down
			}
			lastNum = num

			rest := strings.TrimSpace(text[len(m[0]):])

			// Embedded answer: some bloggers put the answer in a <b> tag on the
			// same div as the clue number. Extract immediately rather than waiting
			// for a separate answer div.
			boldText := strings.TrimSpace(div.Find("b,strong").First().Text())
			if boldText != "" && boldText == strings.ToUpper(boldText) && current != nil {
				answer := cleanEmbeddedAnswer(boldText)
				if answer != "" {
					explanation := ""
					if idx := strings.Index(rest, boldText); idx != -1 {
						explanation = strings.TrimSpace(rest[idx+len(boldText):])
					}
					*current = append(*current, Clue{Number: num, Answer: answer, Explanation: explanation})
				}
				pending = nil
				return
			}

			// Separate clue/answer divs format: store clue and wait for answer div.
			clueText := rest
			letterCount := ""
			if lm := letterCountRe.FindString(clueText); lm != "" {
				letterCount = strings.Trim(strings.TrimSpace(lm), "()")
				clueText = strings.TrimSpace(letterCountRe.ReplaceAllString(clueText, ""))
			}
			pending = &Clue{Number: num, Text: clueText, LetterCount: letterCount}
			return
		}

		// Answer div: must follow a clue div.
		if pending == nil {
			return
		}

		// Format D: bold tag carries the answer.
		boldText := strings.TrimSpace(div.Find("b,strong").First().Text())
		var rawAnswer string
		if boldText != "" && boldText == strings.ToUpper(boldText) {
			rawAnswer = boldText
		} else {
			// Format E: plain text — take the leading run of all-caps words.
			// Single-letter all-caps words (A, I) after the first word are treated
			// as the start of the explanation, not part of the answer.
			words := strings.Fields(text)
			var ans []string
			for i, w := range words {
				if w == strings.ToUpper(w) && (i == 0 || len(w) > 1) {
					ans = append(ans, w)
				} else {
					break
				}
			}
			if len(ans) > 0 {
				rawAnswer = strings.Join(ans, " ")
			}
		}

		if rawAnswer == "" {
			pending = nil
			return
		}
		answer := cleanEmbeddedAnswer(rawAnswer)
		if answer == "" {
			pending = nil
			return
		}

		explanation := ""
		if idx := strings.Index(text, rawAnswer); idx != -1 {
			rest := strings.TrimLeft(text[idx+len(rawAnswer):], " –—-")
			explanation = strings.TrimSpace(rest)
		}
		pending.Answer = answer
		pending.Explanation = explanation
		if current != nil {
			*current = append(*current, *pending)
		}
		pending = nil
	})
	return
}

// segPlainText strips HTML tags from a fragment and normalises non-breaking
// spaces (U+00A0) so the result is safe to feed into regexp matchers.
func segPlainText(html string) string {
	s := htmlTagRe.ReplaceAllString(html, "")
	s = strings.ReplaceAll(s, "\u00a0", " ")
	return strings.TrimSpace(s)
}

// parsePlainProse handles old blog posts where clues appear as plain text in
// the entry-content div, separated by <br/> tags rather than table cells.
// The answer follows the clue number in capital letters (with optional wordplay
// notation), then an explanation.
//
// Sub-formats:
//
//	P1/P4: "N CAPS_ANSWER   explanation"    (3+ spaces separator)
//	P2:    "N\tCAPS_ANSWER – explanation"   (tab / en-dash separator)
//	P3:    number alone on one line; answer on the next
//	P5:    "N   <b>ANSWER</b> explanation"  (bold-tagged answer, NBSP after num)
//
// Direction sections are marked by standalone "Across" / "Down" lines.
func parsePlainProse(doc *goquery.Document) (across, down []Clue) {
	var current *[]Clue
	prevLineNum := 0

	// processSegment receives both the plain text of a <br/>-separated line and
	// the bold text found within it (empty string if none). Bold text is used
	// directly as the answer when present, avoiding ambiguity with the
	// all-caps prefix heuristic.
	processSegment := func(plain, bold string) {
		plain = strings.TrimSpace(plain)
		if plain == "" {
			prevLineNum = 0
			return
		}

		lower := strings.ToLower(plain)
		if lower == "across" {
			current = &across
			prevLineNum = 0
			return
		}
		if lower == "down" {
			current = &down
			prevLineNum = 0
			return
		}
		if current == nil {
			current = &across
		}

		extractAnswer := func(text, boldHint string) (answer, explanation string) {
			// Prefer bold hint when it looks like an all-caps answer.
			if boldHint != "" && boldHint == strings.ToUpper(boldHint) {
				answer = cleanEmbeddedAnswer(boldHint)
				if idx := strings.Index(text, boldHint); idx != -1 {
					explanation = strings.TrimSpace(text[idx+len(boldHint):])
				}
				return
			}
			// proseSepRe separators (dash, equals, multi-space, tab, etc.)
			if loc := proseSepRe.FindStringIndex(text); loc != nil {
				rawAns := strings.TrimSpace(text[:loc[0]])
				lastChar := text[loc[1]-1]
				if (lastChar >= 'A' && lastChar <= 'Z') || lastChar == '(' {
					explanation = strings.TrimSpace(text[loc[1]-1:])
				} else {
					explanation = strings.TrimSpace(text[loc[1]:])
				}
				answer = cleanEmbeddedAnswer(rawAns)
				return
			}
			// All-caps prefix fallback: stop at the first word that is not all-caps,
			// treating a lone uppercase letter (A, I) after the first word as the
			// start of the explanation rather than part of the answer.
			words := strings.Fields(text)
			var ans []string
			for i, w := range words {
				if w == strings.ToUpper(w) && (i == 0 || len(w) > 1) {
					ans = append(ans, w)
				} else {
					break
				}
			}
			if len(ans) > 0 {
				raw := strings.Join(ans, " ")
				answer = cleanEmbeddedAnswer(raw)
				if idx := strings.Index(text, raw); idx != -1 {
					explanation = strings.TrimSpace(text[idx+len(raw):])
				}
			}
			return
		}

		// P3: previous line was a lone number.
		if prevLineNum > 0 {
			if answer, expl := extractAnswer(plain, bold); answer != "" {
				*current = append(*current, Clue{Number: prevLineNum, Answer: answer, Explanation: expl})
			}
			prevLineNum = 0
			return
		}

		// Check for a bare number (P3 clue line).
		if n, err := strconv.Atoi(plain); err == nil && n > 0 && n < 50 {
			prevLineNum = n
			return
		}

		// P1 / P2 / P4 / P5: line starts with "N[whitespace] …"
		m := clueNumRe.FindStringSubmatch(plain)
		if m == nil {
			prevLineNum = 0
			return
		}
		num, _ := strconv.Atoi(strings.TrimSpace(m[1]))
		rest := strings.TrimSpace(plain[len(m[0]):])
		if answer, expl := extractAnswer(rest, bold); answer != "" {
			*current = append(*current, Clue{Number: num, Answer: answer, Explanation: expl})
		}
		prevLineNum = 0
	}

	doc.Find(".entry-content p").Each(func(_ int, p *goquery.Selection) {
		// Work on the raw HTML so we can split on <br> variants before stripping
		// tags, and extract bold text per segment before it is lost.
		pHTML, _ := p.Html()
		const sentinel = "\uE000"
		pHTML = strings.ReplaceAll(pHTML, "<br/>", sentinel)
		pHTML = strings.ReplaceAll(pHTML, "<br />", sentinel)
		pHTML = strings.ReplaceAll(pHTML, "<br>", sentinel)
		for _, seg := range strings.Split(pHTML, sentinel) {
			bold := ""
			if m := boldTagRe.FindStringSubmatch(seg); m != nil {
				bold = strings.TrimSpace(m[1])
			}
			processSegment(segPlainText(seg), bold)
		}
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
			clean := strings.NewReplacer(",", "", ";", "").Replace(m[1])
			xwd.PuzzleNumber, _ = strconv.Atoi(clean)
		}
		if xwd.PuzzleNumber == 0 {
			xwd.PuzzleNumber = puzzleNumFromURL(url)
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
		// Strip both comma and semicolon thousands separators (e.g. "26;971" → 26971)
		clean := strings.NewReplacer(",", "", ";", "").Replace(m[1])
		xwd.PuzzleNumber, _ = strconv.Atoi(clean)
	}
	// Fallback: extract from URL slug when title regex fails (e.g. "Times – 23945")
	if xwd.PuzzleNumber == 0 {
		xwd.PuzzleNumber = puzzleNumFromURL(url)
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

	// Old format: <table cellspacing="N"> (N = 3, 4, or 5) or <table border="0" cellpadding="0">
	if len(xwd.Across) == 0 && len(xwd.Down) == 0 {
		doc.Find("table[cellspacing='3'],table[cellspacing='4'],table[cellspacing='5'],table[border='0'][cellpadding='0']").Each(func(_ int, table *goquery.Selection) {
			// Skip tables nested inside another table (answer cells sometimes contain
			// formatting sub-tables). ParentsFiltered checks ancestors only, not self.
			if table.ParentsFiltered("table").Length() > 0 {
				return
			}
			ac, dn := parseOldFormatTable(table.Find("tr"))
			if len(xwd.Across) == 0 {
				xwd.Across = ac
				xwd.Down = dn
			} else if len(xwd.Down) == 0 {
				if len(dn) == 0 && len(ac) > 0 {
					xwd.Down = ac
				} else {
					xwd.Down = dn
				}
			}
		})
	}

	// lj-spoiler format: Saturday posts (2021+) put clues inside a spoiler widget.
	// Go's HTML parser hoists tables out of inline <span> elements, so the clue tables
	// end up as siblings of the <p> that contains the .lj-spoiler widget.
	// We locate them via .lj-spoiler's parent's following siblings.
	if len(xwd.Across) == 0 && len(xwd.Down) == 0 {
		doc.Find(".lj-spoiler").Each(func(_ int, spoiler *goquery.Selection) {
			spoiler.Parent().NextAll().Filter("table").Each(func(_ int, table *goquery.Selection) {
				if table.ParentsFiltered("table").Length() > 0 {
					return
				}
				ac, dn := parseOldFormatTable(table.Find("tr"))
				if len(xwd.Across) == 0 {
					xwd.Across = ac
					xwd.Down = dn
				} else if len(xwd.Down) == 0 {
					if len(dn) == 0 && len(ac) > 0 {
						xwd.Down = ac
					} else {
						xwd.Down = dn
					}
				}
			})
		})
	}

	// Div-prose fallback: some bloggers use alternating <div> blocks for clue and answer.
	if len(xwd.Across) == 0 && len(xwd.Down) == 0 {
		xwd.Across, xwd.Down = parseDivProse(doc)
	}

	// Plain-prose fallback: old posts where clues are in <br/>-separated paragraphs.
	// The answer follows the clue number in capital letters.
	if len(xwd.Across) == 0 && len(xwd.Down) == 0 {
		xwd.Across, xwd.Down = parsePlainProse(doc)
	}

	return xwd
}
