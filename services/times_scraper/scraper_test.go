package main

import (
	"strings"
	"testing"

	"github.com/PuerkitoBio/goquery"
)

// ---------------------------------------------------------------------------
// cleanEmbeddedAnswer
// ---------------------------------------------------------------------------

func TestCleanEmbeddedAnswer(t *testing.T) {
	tests := []struct {
		input string
		want  string
	}{
		// Simple cases
		{"MYSTERY", "MYSTERY"},
		{"FIRST STAGE", "FIRST STAGE"},

		// Comma notation
		{"CHAR,LOCK", "CHARLOCK"},

		// Uppercase parens kept
		{"L(ARDO)ON", "LARDOON"},
		{"TH(RIFT)Y", "THRIFTY"},
		{"(A,L)OE", "ALOE"},

		// Lowercase parens stripped
		{"SACK(lish)", "SACK"},
		{"SACK(l)", "SACK"},
		{"BOMB(Sub reversed)", "BOMB"},
		{"DEACON(deacon is)*", "DEACON"},

		// Mixed-case paren stripped
		{"BOMB(Sub)", "BOMB"},

		// Plus notation
		{"H + AVE", "HAVE"},
		{"PO + SE", "POSE"},

		// Star stripped
		{"MYSTERY*", "MYSTERY"},

		// Empty / non-answer
		{"", ""},
		{"lower", ""},
		{"123", ""},
	}

	for _, tc := range tests {
		got := cleanEmbeddedAnswer(tc.input)
		if got != tc.want {
			t.Errorf("cleanEmbeddedAnswer(%q) = %q, want %q", tc.input, got, tc.want)
		}
	}
}

// ---------------------------------------------------------------------------
// puzzleNumFromURL
// ---------------------------------------------------------------------------

func TestPuzzleNumFromURL(t *testing.T) {
	tests := []struct {
		url  string
		want int
	}{
		{"https://example.com/times-cryptic-29503", 29503},
		{"https://example.com/times-cryptic-no-27282-saturday-12-june-2021", 27282},
		{"https://example.com/120430", 120430},
		{"https://example.com/no-numbers-here", 0},
		{"https://example.com/short-12", 0}, // 2 digits — too short
	}

	for _, tc := range tests {
		got := puzzleNumFromURL(tc.url)
		if got != tc.want {
			t.Errorf("puzzleNumFromURL(%q) = %d, want %d", tc.url, got, tc.want)
		}
	}
}

// ---------------------------------------------------------------------------
// parseEmbeddedClueRow (Pattern A)
// ---------------------------------------------------------------------------

func TestParseEmbeddedClueRow(t *testing.T) {
	tests := []struct {
		name        string
		num         int
		cellText    string
		wantAnswer  string
		wantExpl    string
		wantOK      bool
	}{
		{
			name:       "en-dash separator",
			num:        3,
			cellText:   "MYSTERY – hidden in most erywhile",
			wantAnswer: "MYSTERY",
			wantExpl:   "hidden in most erywhile",
			wantOK:     true,
		},
		{
			name:       "equals separator",
			num:        7,
			cellText:   "CHARLOCK = CHAR,LOCK – mustard plant",
			wantAnswer: "CHARLOCK",
			wantExpl:   "CHAR,LOCK – mustard plant",
			wantOK:     true,
		},
		{
			name:       "no separator — answer only",
			num:        1,
			cellText:   "ANSWER",
			wantAnswer: "ANSWER",
			wantExpl:   "",
			wantOK:     true,
		},
		{
			name:    "empty cell",
			num:     1,
			cellText: "",
			wantOK:  false,
		},
		{
			name:    "lowercase only — not an answer",
			num:     1,
			cellText: "not an answer at all",
			wantOK:  false,
		},
	}

	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			clue, ok := parseEmbeddedClueRow(tc.num, tc.cellText)
			if ok != tc.wantOK {
				t.Fatalf("ok = %v, want %v", ok, tc.wantOK)
			}
			if !ok {
				return
			}
			if clue.Answer != tc.wantAnswer {
				t.Errorf("Answer = %q, want %q", clue.Answer, tc.wantAnswer)
			}
			if clue.Explanation != tc.wantExpl {
				t.Errorf("Explanation = %q, want %q", clue.Explanation, tc.wantExpl)
			}
			if clue.Number != tc.num {
				t.Errorf("Number = %d, want %d", clue.Number, tc.num)
			}
		})
	}
}

// ---------------------------------------------------------------------------
// parseDivProse
// ---------------------------------------------------------------------------

func parseDivProseHTML(html string) ([]Clue, []Clue) {
	doc, err := goquery.NewDocumentFromReader(strings.NewReader(html))
	if err != nil {
		panic(err)
	}
	return parseDivProse(doc)
}

func TestParseDivProse_WithHeaders(t *testing.T) {
	html := `<div class="entry-content">
		<div>Across</div>
		<div>1 Sounds like a fishy tale (4)</div>
		<div><b>CARP</b> homophone of carp (complain)</div>
		<div>3 Bird sanctuary (4)</div>
		<div><b>NEST</b> double definition</div>
		<div>Down</div>
		<div>2 Regal headwear (5)</div>
		<div><b>CROWN</b> cryptic def</div>
	</div>`

	across, down := parseDivProseHTML(html)

	if len(across) != 2 {
		t.Fatalf("across: got %d clues, want 2", len(across))
	}
	if across[0].Number != 1 || across[0].Answer != "CARP" {
		t.Errorf("across[0] = %+v", across[0])
	}
	if across[1].Number != 3 || across[1].Answer != "NEST" {
		t.Errorf("across[1] = %+v", across[1])
	}

	if len(down) != 1 {
		t.Fatalf("down: got %d clues, want 1", len(down))
	}
	if down[0].Number != 2 || down[0].Answer != "CROWN" {
		t.Errorf("down[0] = %+v", down[0])
	}
}

func TestParseDivProse_NoHeaders_NumberReset(t *testing.T) {
	// When there are no Across/Down headers, a number reset implies direction change.
	html := `<div class="entry-content">
		<div>1 First across clue (4)</div>
		<div>WORD explanation here</div>
		<div>2 Second across clue (5)</div>
		<div>WORDS explanation here</div>
		<div>1 First down clue (3)</div>
		<div>CAT explanation here</div>
	</div>`

	across, down := parseDivProseHTML(html)

	if len(across) != 2 {
		t.Fatalf("across: got %d, want 2", len(across))
	}
	if len(down) != 1 {
		t.Fatalf("down: got %d, want 1", len(down))
	}
	if down[0].Number != 1 || down[0].Answer != "CAT" {
		t.Errorf("down[0] = %+v", down[0])
	}
}

func TestParseDivProse_PlainTextAnswer(t *testing.T) {
	// Format E: answer is plain all-caps text, not in a bold tag.
	html := `<div class="entry-content">
		<div>Across</div>
		<div>5 A puzzling word (6)</div>
		<div>ENIGMA some explanation follows here</div>
	</div>`

	across, _ := parseDivProseHTML(html)

	if len(across) != 1 {
		t.Fatalf("got %d clues, want 1", len(across))
	}
	if across[0].Answer != "ENIGMA" {
		t.Errorf("Answer = %q, want ENIGMA", across[0].Answer)
	}
}

// ---------------------------------------------------------------------------
// parsePlainProse
// ---------------------------------------------------------------------------

func parsePlainProseHTML(html string) ([]Clue, []Clue) {
	doc, err := goquery.NewDocumentFromReader(strings.NewReader(html))
	if err != nil {
		panic(err)
	}
	return parsePlainProse(doc)
}

func TestParsePlainProse_TabSeparator(t *testing.T) {
	// P2: number + tab + answer + dash + explanation
	html := `<div class="entry-content">
		<p>Across<br/>
		1	ANSWER – some explanation<br/>
		3	WORD – another clue</p>
		<p>Down<br/>
		2	CLUE – down explanation</p>
	</div>`

	across, down := parsePlainProseHTML(html)

	if len(across) != 2 {
		t.Fatalf("across: got %d, want 2", len(across))
	}
	if across[0].Number != 1 || across[0].Answer != "ANSWER" {
		t.Errorf("across[0] = %+v", across[0])
	}
	if len(down) != 1 || down[0].Answer != "CLUE" {
		t.Errorf("down = %+v", down)
	}
}

func TestParsePlainProse_MultiSpaceSeparator(t *testing.T) {
	// P1/P4: number + spaces + answer + 3+ spaces + explanation
	html := `<div class="entry-content">
		<p>Across<br/>
		1 MYSTERY   hidden in most erywhile<br/>
		2 FIRST STAGE   double definition</p>
	</div>`

	across, _ := parsePlainProseHTML(html)

	if len(across) != 2 {
		t.Fatalf("got %d, want 2", len(across))
	}
	if across[0].Answer != "MYSTERY" {
		t.Errorf("across[0].Answer = %q, want MYSTERY", across[0].Answer)
	}
	if across[1].Answer != "FIRST STAGE" {
		t.Errorf("across[1].Answer = %q, want FIRST STAGE", across[1].Answer)
	}
}

func TestParsePlainProse_BoldAnswer(t *testing.T) {
	// P5: bold-tagged answer after clue number
	html := `<div class="entry-content">
		<p>Across<br/>
		1&#160;<b>BOLD</b> cryptic explanation here</p>
	</div>`

	across, _ := parsePlainProseHTML(html)

	if len(across) != 1 {
		t.Fatalf("got %d, want 1", len(across))
	}
	if across[0].Number != 1 || across[0].Answer != "BOLD" {
		t.Errorf("clue = %+v", across[0])
	}
}

// ---------------------------------------------------------------------------
// parseProse (Saturday WP REST API format)
// ---------------------------------------------------------------------------

func parseProseHTML(html string) ([]Clue, []Clue) {
	doc, err := goquery.NewDocumentFromReader(strings.NewReader(html))
	if err != nil {
		panic(err)
	}
	return parseProse(doc)
}

func TestParseProse_BasicAcrossDown(t *testing.T) {
	html := `<div>
		<p><b><span style="font-size:18.0pt;">Across</span></b></p>
		<p>
			<span style="color:blue;">1 A clue text (7)</span><br/>
			<b>MYSTERY:</b> hidden word
		</p>
		<p>
			<span style="color:blue;">4 Another clue (5)</span><br/>
			<b>STONE:</b> double def
		</p>
		<p><b><span style="font-size:18.0pt;">Down</span></b></p>
		<p>
			<span style="color:blue;">2 Down clue (6)</span><br/>
			<b>ENIGMA:</b> anagram
		</p>
	</div>`

	across, down := parseProseHTML(html)

	if len(across) != 2 {
		t.Fatalf("across: got %d, want 2", len(across))
	}
	if across[0].Number != 1 || across[0].Answer != "MYSTERY" {
		t.Errorf("across[0] = %+v", across[0])
	}
	if len(down) != 1 {
		t.Fatalf("down: got %d, want 1", len(down))
	}
}
