import type { EtlCopilotPreviewResponse } from "@/services/projectService";

export interface SavedRecipe {
  prompt: string;
  preview?: EtlCopilotPreviewResponse | null;
  timestamp: number;
}

interface SavedRecipesProps {
  recipes: SavedRecipe[];
  onSelect: (recipe: SavedRecipe) => void;
  onClear: () => void;
}

export default function SavedRecipes({
  recipes,
  onSelect,
  onClear,
}: SavedRecipesProps) {
  return (
    <section className="rounded-xl border border-white/[0.08] bg-black/20 p-4">
      <div className="mb-3 flex items-center justify-between gap-3">
        <h4 className="text-sm font-semibold text-cyan-300">Saved Recipes</h4>
        {recipes.length > 0 && (
          <button
            type="button"
            onClick={onClear}
            className="text-xs text-red-400 hover:text-red-300"
          >
            Clear History
          </button>
        )}
      </div>

      {recipes.length === 0 ? (
        <p className="text-sm text-slate-500">No saved recipes yet</p>
      ) : (
        <div className="space-y-2">
          {recipes.map((recipe, index) => (
            <button
              key={`${recipe.timestamp}-${index}`}
              type="button"
              onClick={() => onSelect(recipe)}
              className="w-full rounded-lg border border-white/10 bg-white/[0.02] p-3 text-left transition hover:border-cyan-500/40"
            >
              <span className="block break-words text-sm text-white">
                {recipe.prompt}
              </span>
              <span className="mt-1 block text-xs text-slate-500">
                {Number.isFinite(recipe.timestamp)
                  ? new Date(recipe.timestamp).toLocaleString()
                  : ""}
              </span>
            </button>
          ))}
        </div>
      )}
    </section>
  );
}