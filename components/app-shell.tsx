"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { Activity, BarChart3, ClipboardList, Database, MapPinned, Newspaper } from "lucide-react";
import { cn } from "@/lib/utils";

const navItems = [
  { href: "/coverage", label: "Coverage", icon: MapPinned },
  { href: "/research", label: "Research", icon: Newspaper },
  { href: "/review", label: "Review", icon: ClipboardList },
  { href: "/pipeline", label: "Pipeline", icon: Database },
  { href: "/activity", label: "Activity", icon: Activity },
  { href: "/dashboard", label: "Dashboard", icon: BarChart3 }
];

type AppShellProps = {
  children: React.ReactNode;
};

export function AppShell({ children }: AppShellProps) {
  const pathname = usePathname();

  return (
    <div className="min-h-dvh bg-slate-50 text-slate-950">
      <aside className="fixed inset-y-0 left-0 hidden w-56 border-r border-slate-200 bg-white px-3 py-4 md:block">
        <div className="mb-5 px-2">
          <p className="text-sm font-semibold">TCG Pipeline</p>
          <p className="text-xs text-slate-500">Research workspace</p>
        </div>
        <nav aria-label="Main navigation" className="space-y-1">
          {navItems.map((item) => (
            <Link
              className={cn(
                "flex items-center gap-2 rounded-md px-2 py-2 text-sm text-slate-700 hover:bg-slate-100",
                pathname.startsWith(item.href) && "bg-slate-100 font-medium text-slate-950"
              )}
              href={item.href}
              key={item.href}
            >
              <item.icon className="size-4" aria-hidden="true" />
              {item.label}
            </Link>
          ))}
        </nav>
      </aside>
      <div className="md:pl-56">
        <header className="border-b border-slate-200 bg-white px-5 py-3 md:hidden">
          <p className="text-sm font-semibold">TCG Pipeline</p>
        </header>
        {children}
      </div>
    </div>
  );
}
