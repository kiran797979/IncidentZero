import React from "react";
import "./StarBorder.css";

// 1. Separate custom props for clean TypeScript definitions
type CustomStarBorderProps<T extends React.ElementType> = {
  as?: T;
  className?: string;
  children?: React.ReactNode;
  color?: string;
  speed?: React.CSSProperties["animationDuration"];
  thickness?: number;
};

// 2. Combine custom props with standard HTML attributes safely
export type StarBorderProps<T extends React.ElementType> = CustomStarBorderProps<T> &
  Omit<React.ComponentPropsWithoutRef<T>, keyof CustomStarBorderProps<T>>;

const StarBorder = <T extends React.ElementType = "button">({
  as,
  className = "",
  color = "white",
  speed = "6s",
  thickness = 1,
  children,
  style, // Extract style specifically so we don't have to use 'any'
  ...rest
}: StarBorderProps<T>) => {
  const Component = as || "button";

  return (
    <Component
      className={`star-border-container ${className}`.trim()}
      style={{ padding: `${thickness}px 0`, ...style }}
      {...rest}
    >
      <div
        className="border-gradient-bottom"
        style={{
          background: `radial-gradient(circle, ${color}, transparent 10%)`,
          animationDuration: speed,
        }}
      />
      <div
        className="border-gradient-top"
        style={{
          background: `radial-gradient(circle, ${color}, transparent 10%)`,
          animationDuration: speed,
        }}
      />
      <div className="star-border-inner">{children}</div>
    </Component>
  );
};

export default StarBorder;