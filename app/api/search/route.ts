import { source } from '@/lib/source';
import { createFromSource } from 'fumadocs-core/search/server';


// Imported repositories can contain pages that normalize to the same URL
// (usually because an upstream filename differs only by punctuation). Orama
// requires globally unique document IDs, so keep the first canonical page for
// each URL when building the search index.
const searchSource = Object.assign(Object.create(source), {
  getPages(...args: Parameters<typeof source.getPages>) {
    const pages = source.getPages(...args);
    const seen = new Set<string>();
    const unique = pages.filter((page) => {
      if (seen.has(page.url)) return false;
      seen.add(page.url);
      return true;
    });
    return unique;
  },
});

// Fumadocs adds ``-0``, ``-1``, ... to heading/content document IDs. A page
// whose URL itself ends in ``-0`` can otherwise collide with those generated
// IDs, so terminate page IDs with a delimiter before the suffixes are added.
export const { GET } = createFromSource(searchSource, {
  buildIndex: async (page) => {
    const structuredData = page.data.structuredData;
    if (!structuredData) throw new Error(`Missing structured search data for ${page.url}`);
    return {
      title: page.data.title ?? page.path,
      description: page.data.description,
      url: page.url,
      id: `${page.url}|`,
      structuredData: typeof structuredData === 'function' ? await structuredData() : structuredData,
    };
  },
});
