import type { ReactNode } from 'react';

interface Props {
  title: string;
  children: ReactNode;
}

export function MissingData({ title, children }: Props) {
  return (
    <div className="missing-data">
      <h4>⚠️ {title}</h4>
      <p>{children}</p>
    </div>
  );
}
