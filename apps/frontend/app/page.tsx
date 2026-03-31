import Link from "next/link";
import { listPuzzles, type PuzzleSummary } from "@/lib/api";

export default async function Home() {
  let puzzles: PuzzleSummary[] = [];
  let error: string | null = null;

  try {
    puzzles = await listPuzzles(50);
  } catch (e) {
    error = "Could not connect to API. Is the backend running?";
  }

  return (
    <main className="min-h-screen bg-gray-50">
      <header className="bg-white border-b border-gray-200 px-6 py-4">
        <h1 className="text-2xl font-bold tracking-tight text-gray-900">Clue Gun</h1>
        <p className="text-sm text-gray-500 mt-0.5">Times Cryptic Crossword Archive</p>
      </header>

      <div className="max-w-4xl mx-auto px-6 py-8">
        {error ? (
          <p className="text-red-600 bg-red-50 border border-red-200 rounded p-4">{error}</p>
        ) : puzzles.length === 0 ? (
          <p className="text-gray-500">No puzzles loaded yet. Run the scraper first.</p>
        ) : (
          <div className="bg-white rounded-lg border border-gray-200 divide-y divide-gray-100">
            {puzzles.map((p) => (
              <Link
                key={p.puzzle_number}
                href={`/puzzles/${p.puzzle_number}`}
                className="flex items-center justify-between px-5 py-3.5 hover:bg-gray-50 transition-colors"
              >
                <div>
                  <span className="font-semibold text-gray-900">#{p.puzzle_number}</span>
                  {p.puzzle_date && (
                    <span className="ml-3 text-sm text-gray-500">{p.puzzle_date}</span>
                  )}
                  {p.blogger && (
                    <span className="ml-3 text-sm text-gray-400">{p.blogger}</span>
                  )}
                </div>
                <span className="text-xs text-gray-400">
                  {p.across_count}A / {p.down_count}D
                </span>
              </Link>
            ))}
          </div>
        )}
      </div>
    </main>
  );
}
