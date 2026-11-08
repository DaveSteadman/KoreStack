# Dazzle Line UI Icons

UI icons must come from [dazzle-line](./dazzle-line/), except for explicitly approved assets.
The source collection is [Dazzle Line Icons on SVG Repo](https://www.svgrepo.com/collection/dazzle-line-icons/).
Use that collection when sourcing additional icons. Do not invent inline SVGs,
use emoji substitutes, or mix in another icon pack.

Approved exception: the diagram editor's Pan tool uses the supplied
[Gentlecons hand](./Gentlecons/hand-svgrepo-com.svg), because the Dazzle Line
collection has no hand/pan icon. Do not copy it into Dazzle Line or mislabel its source.

When an icon is missing, report it before choosing a substitute. Download approved
additions from their SVG Repo pages into `dazzle-line/`, retaining the original SVG
and the `<name>-svgrepo-com.svg` naming convention. Verify each asset's license and
record its source URL and license in the pack manifest.

Rendered diagrams, graph edges, document content and textual status values are
not UI icon assets and should not be replaced with pictograms.

## Usage

### URL (HTML/CSS)

Use direct URL:

`/ui-elements/assets/icons/dazzle-line/<icon-name>.svg`

Example:

```html
<span class="kcui-icon kcui-icon--asset" aria-hidden="true"
      style="--kcui-icon-size:16px; --kcui-icon-url:url(/ui-elements/assets/icons/dazzle-line/alarm-clock-alt-svgrepo-com.svg)"></span>
```

```css
.my-icon {
  width: 16px;
  height: 16px;
  background: currentColor;
  -webkit-mask: url('/ui-elements/assets/icons/dazzle-line/alarm-clock-alt-svgrepo-com.svg') center / contain no-repeat;
  mask: url('/ui-elements/assets/icons/dazzle-line/alarm-clock-alt-svgrepo-com.svg') center / contain no-repeat;
}
```

### JS helper

```js
import { svgIconMask, svgIconUrl } from '/ui-elements/assets/js/chrome.js';

const markup = svgIconMask('alarm-clock-alt-svgrepo-com', { size: 16 });
const url = svgIconUrl('alarm-clock-alt-svgrepo-com');
```

Use masks for monochrome UI icons so they inherit the control's colour in both
light and dark themes. Put the icon directly inside an icon-only button, and give
the button an accessible label.
