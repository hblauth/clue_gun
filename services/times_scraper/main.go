package main

import (
	"fmt"
	"io"
	"net/http"
	"os"
)

func main() {
	if len(os.Args) < 2 {
		fmt.Fprintln(os.Stderr, "Usage: go run main.go <url>")
		os.Exit(1)
	}
	url := os.Args[1]
	resp, err := http.Get(url)
	if err != nil {
		fmt.Fprintln(os.Stderr, "Error fetching URL:", err)
		os.Exit(1)
	}
	defer resp.Body.Close()
	_, err = io.Copy(os.Stdout, resp.Body)
	if err != nil {
		fmt.Fprintln(os.Stderr, "Error reading response:", err)
		os.Exit(1)
	}
}
