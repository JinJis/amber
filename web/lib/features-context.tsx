"use client";

// Client-side access to the feature flags resolved server-side (lib/features.getFeatures). The root
// client component (Chat) wraps its tree in <FeaturesProvider>; any descendant reads useFeatures().

import { createContext, useContext, type ReactNode } from "react";
import type { Features } from "./features";

const FeaturesContext = createContext<Features>({ dashboard: true, alerts: true });

export function FeaturesProvider({ value, children }: { value: Features; children: ReactNode }) {
  return <FeaturesContext.Provider value={value}>{children}</FeaturesContext.Provider>;
}

export const useFeatures = (): Features => useContext(FeaturesContext);
