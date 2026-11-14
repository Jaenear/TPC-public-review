/** Poker hand classes and notation. Counts combinations only; never computes GTO. */
export const RANKS = [
  "A",
  "K",
  "Q",
  "J",
  "T",
  "9",
  "8",
  "7",
  "6",
  "5",
  "4",
  "3",
  "2",
];
export const HANDS = RANKS.flatMap((rank, row) =>
  RANKS.map((other, column) =>
    row === column
      ? rank + other
      : row < column
        ? rank + other + "s"
        : other + rank + "o",
  ),
);
export const handCombos = (hand) =>
  hand.length === 2 ? 6 : hand.endsWith("s") ? 4 : 12;
export function rangeStats(selected) {
  const combinations = selected.reduce(
    (total, hand) => total + handCombos(hand),
    0,
  );
  return {
    classes: selected.length,
    combinations,
    percentage: (combinations / 1326) * 100,
  };
}
export function parseRange(notation) {
  const selected = new Set();
  const parts = notation
    .trim()
    .split(/[\s,;]+/)
    .filter(Boolean);
  for (let raw of parts) {
    raw = raw.replace(/10/g, "T");
    const match = raw.match(/^([AKQJT2-9])([AKQJT2-9])([so])?(\+)?$/i);
    if (!match)
      throw new Error(`잘못된 핸드 표기: ${raw}. 예: AA, AKs, AQo, 88+`);
    const first = match[1].toUpperCase(),
      second = match[2].toUpperCase(),
      suit = match[3]?.toLowerCase();
    const row = RANKS.indexOf(first),
      column = RANKS.indexOf(second);
    if (row > column || (row === column && suit))
      throw new Error(
        `핸드 표기를 확인해주세요: ${raw}. 높은 카드를 먼저 적어주세요.`,
      );
    if (row === column) {
      if (match[4])
        for (let i = 0; i <= row; i++) selected.add(RANKS[i] + RANKS[i]);
      else selected.add(first + second);
    } else {
      const suits = suit ? [suit] : ["s", "o"];
      const low = match[4] ? row + 1 : column;
      for (let i = low; i <= column; i++)
        for (const type of suits) selected.add(first + RANKS[i] + type);
    }
  }
  return HANDS.filter((hand) => selected.has(hand));
}
export const EXAMPLE_RANGE = parseRange("88+, ATs+, KQs, AJo+");
