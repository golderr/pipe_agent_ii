import * as React from "react";
import { cva, type VariantProps } from "class-variance-authority";
import { cn } from "@/lib/utils";

const buttonVariants = cva(
  "inline-flex h-9 items-center justify-center gap-2 rounded-md border px-3 text-sm font-medium transition-colors focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 disabled:pointer-events-none disabled:opacity-50",
  {
    variants: {
      variant: {
        default:
          "border-transparent bg-teal-700 text-white hover:bg-teal-800 focus-visible:outline-teal-700",
        outline:
          "border-slate-300 bg-white text-slate-800 hover:bg-slate-50 focus-visible:outline-slate-500",
        ghost:
          "border-transparent bg-transparent text-slate-700 hover:bg-slate-100 focus-visible:outline-slate-500"
      }
    },
    defaultVariants: {
      variant: "default"
    }
  }
);

export type ButtonProps = React.ButtonHTMLAttributes<HTMLButtonElement> &
  VariantProps<typeof buttonVariants>;

export function Button({ className, variant, ...props }: ButtonProps) {
  return <button className={cn(buttonVariants({ variant }), className)} {...props} />;
}
