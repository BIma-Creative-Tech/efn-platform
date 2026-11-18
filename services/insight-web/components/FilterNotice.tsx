export function FilterNotice({ adjusted }: { adjusted: boolean }) {
  if (!adjusted) return null;
  return (
    <p className="notice" role="status">
      Filter tidak valid, dikembalikan ke bawaan.
    </p>
  );
}
