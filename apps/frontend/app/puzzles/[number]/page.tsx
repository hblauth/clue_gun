import Link from "next/link";
import { notFound } from "next/navigation";
import { getPuzzle, type Clue } from "@/lib/api";

interface Props {
  params: Promise<{ number: string }>;
}

function ClueTable({ clues, title }: { clues: Clue[]; title: string }) {
  if (!clues.length) return null;
  return (
    <section className="mb-8">
      <h2 className="text-lg font-semibold text-gray-700 mb-3">{title}</h2>
      <div className="bg-white rounded-lg border border-gray-200 divide-y divide-gray-100">
        {clues.map((clue) => (
          <div key={clue.number} className="px-5 py-3">
            <div className="flex items-baseline gap-3">
              <span className="text-sm font-bold text-gray-400 w-6 shrink-0">
                {clue.number}
              </span>
              <div className="min-w-0">
                {clue.text && (
                  <p className="text-gray-800">
                    {clue.text}
                    {clue.letter_count && (
                      <span className="text-gray-400 ml-1">({clue.letter_count})</span>
                    )}
                  </p>
                )}
                {clue.answer && (
                  <p className="text-sm font-mono text-emerald-700 mt-0.5">{clue.answer}</p>
                )}
                {clue.explanation && (
                  <p className="text-sm text-gray-400 mt-0.5">{clue.explanation}</p>
                )}
              </div>
            </div>
          </div>
        ))}
      </div>
    </section>
  );
}

export default async function PuzzlePage({ params }: Props) {
  const { number } = await params;
  const puzzleNumber = parseInt(number, 10);

  if (isNaN(puzzleNumber)) notFound();

  let puzzle;
  try {
    puzzle = await getPuzzle(puzzleNumber);
  } catch {
    notFound();
  }

  return (
    <main className="min-h-screen bg-gray-50">
      <header className="bg-white border-b border-gray-200 px-6 py-4">
        <div className="max-w-4xl mx-auto">
          <Link href="/" className="text-sm text-gray-500 hover:text-gray-700">
            ← All puzzles
          </Link>
          <h1 className="text-2xl font-bold text-gray-900 mt-1">
            Times Cryptic #{puzzle.puzzle_number}
          </h1>
          <div className="flex gap-4 text-sm text-gray-500 mt-0.5">
            {puzzle.puzzle_date && <span>{puzzle.puzzle_date}</span>}
            {puzzle.blogger && <span>{puzzle.blogger}</span>}
            <a
              href={puzzle.url}
              target="_blank"
              rel="noopener noreferrer"
              className="hover:text-gray-700 underline underline-offset-2"
            >
              Original post ↗
            </a>
          </div>
        </div>
      </header>

      <div className="max-w-4xl mx-auto px-6 py-8">
        <ClueTable clues={puzzle.across} title="Across" />
        <ClueTable clues={puzzle.down} title="Down" />
      </div>
    </main>
  );
}
