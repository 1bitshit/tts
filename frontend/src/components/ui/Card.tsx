import React, { HTMLAttributes, forwardRef } from 'react';
import { cn } from '../../utils/cn';
import type { LucideIcon } from 'lucide-react';

interface CardProps extends Omit<HTMLAttributes<HTMLDivElement>, 'title'> {
  title?: React.ReactNode;
  icon?: LucideIcon;
}

export const Card = forwardRef<HTMLDivElement, CardProps>(
  ({ className, children, title, icon: Icon, ...props }, ref) => {
    return (
      <div
        ref={ref}
        className={cn(
          'bg-bg-card border border-border-subtle rounded-lg p-xl shadow-card',
          className
        )}
        {...props}
      >
        {(title || Icon) && (
          <div className="mb-lg flex items-center gap-sm">
            {Icon && <Icon className="w-5 h-5 text-accent-cyan" />}
            {title && <h2 className="font-display text-base font-semibold text-text-primary">{title}</h2>}
          </div>
        )}
        {children}
      </div>
    );
  }
);

Card.displayName = 'Card';

interface CardHeaderProps extends HTMLAttributes<HTMLDivElement> {}

export const CardHeader = forwardRef<HTMLDivElement, CardHeaderProps>(
  ({ className, children, ...props }, ref) => {
    return (
      <div
        ref={ref}
        className={cn('flex items-center justify-between mb-lg', className)}
        {...props}
      >
        {children}
      </div>
    );
  }
);

CardHeader.displayName = 'CardHeader';
