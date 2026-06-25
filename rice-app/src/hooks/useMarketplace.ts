/**
 * useMarketplace — re-exported from the global MarketplaceContext so the market
 * screens and "내 스킬" share one catalog (publish from 내 스킬 surfaces in market).
 * The state/logic moved to ../lib/marketplace/MarketplaceContext.
 */
export { useMarketplace, type SkillDraft, type MarketplaceContextValue as UseMarketplaceReturn } from '../lib/marketplace/MarketplaceContext';

// Made with Bob
