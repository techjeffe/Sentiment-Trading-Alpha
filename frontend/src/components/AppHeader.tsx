import type { ReactNode } from "react";

type AppHeaderProps = {
  title?: string;
  titleGradient?: string;
  titleSize?: string;
  subtitle?: string;
  maxWidth?: string;
  py?: string;
  titleSlot?: ReactNode;
  children?: ReactNode;
};

export function AppHeader({
  title,
  titleGradient = "from-blue-400 to-emerald-400",
  titleSize = "text-xl",
  subtitle,
  maxWidth = "max-w-6xl",
  py = "py-3",
  titleSlot,
  children,
}: AppHeaderProps) {
  return (
    <header className="border-b border-slate-800 bg-slate-900/80 backdrop-blur sticky top-0 z-10">
      <div className={`${maxWidth} mx-auto px-6 ${py} flex items-center justify-between gap-4`}>
        {titleSlot ?? (
          <div>
            <h1 className={`${titleSize} font-bold bg-clip-text text-transparent bg-gradient-to-r ${titleGradient}`}>
              {title}
            </h1>
            {subtitle && <p className="text-slate-500 text-xs mt-0.5">{subtitle}</p>}
          </div>
        )}
        {children}
      </div>
    </header>
  );
}
