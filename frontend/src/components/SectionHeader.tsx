interface Props {
  emoji: string;
  title: string;
}

export function SectionHeader({ emoji, title }: Props) {
  return (
    <div className="section-header">
      {emoji} {title}
    </div>
  );
}
