import { Marked } from 'marked'

export const markdownParser = new Marked({
  async: false,
  gfm: true,
  breaks: true,
  tokenizer: {
    del(source) {
      // A single tilde denotes ranges in business text; only ~~ marks a deletion.
      if (!source.startsWith('~~')) return undefined
      return false
    },
  },
})
