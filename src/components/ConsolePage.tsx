import type { ReactNode } from "react";
import type { LucideIcon } from "lucide-react";

type ConsolePageProps = {
  icon: LucideIcon;
  title: string;
  subtitle: string;
  className?: string;
  actions?: ReactNode;
  children: ReactNode;
};

export function ConsolePage({ icon: Icon, title, subtitle, className, actions, children }: ConsolePageProps) {
  return (
    <main className={`admin-page${className ? ` ${className}` : ""}`} aria-label={title}>
      <div className="admin-page-inner">
        <header className="admin-page-header">
          <div className="admin-heading-title">
            <span className="page-icon"><Icon size={19} /></span>
            <div><h1>{title}</h1><p>{subtitle}</p></div>
          </div>
          {actions}
        </header>
        {children}
      </div>
    </main>
  );
}
