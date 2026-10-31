# TerraMaster TOS packaging

`shelfy.lang` holds the App Center texts (name, author, description, release notes, important notes)
in the 14 languages TOS requires: `zh-cn`, `zh-hk`, `en-us`, `fr-fr`, `de-de`, `it-it`, `es-es`,
`hu-hu`, `ja-jp`, `ko-kr`, `pl-pl`, `ru-ru`, `tr-tr`, `pt-pt` (UTF-8 without BOM, LF line endings).

Shelfy's interface ships the same 14 languages. Its codes map to the TOS codes like this, and
`DEFAULT_LANGUAGE` accepts either form:

| TOS | Shelfy |
|---|---|
| `zh-cn` | `zh-Hans` |
| `zh-hk` | `zh-Hant` |
| `en-us`, `fr-fr`, `de-de`, `it-it`, `es-es`, `hu-hu`, `ja-jp`, `ko-kr`, `pl-pl`, `ru-ru`, `tr-tr`, `pt-pt` | `en`, `fr`, `de`, `it`, `es`, `hu`, `ja`, `ko`, `pl`, `ru`, `tr`, `pt` |

Before signing in, Shelfy follows the browser's language when it is one of these.
