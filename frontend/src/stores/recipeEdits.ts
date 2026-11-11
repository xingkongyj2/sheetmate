import { reactive } from 'vue'

import type { RecipeEditDraft } from '../composables/recipeEditing.ts'

/** Stage a target without sending a message; the user submits the actual change in chat. */
export const pendingRecipeEdits = reactive(new Map<string, RecipeEditDraft>())
