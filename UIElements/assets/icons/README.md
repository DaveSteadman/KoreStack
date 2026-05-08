# Icon Packs (Public Mirror)

This folder hosts browser-served mirrors of external SVG icon packs.
Source downloads are stored in `UIElements/SVGicons/`.

## Dazzle Line Import

1. Download the icon zip from SVGRepo manually in a browser.
2. Import into the repository:

```bash
python tools/import_icon_pack.py --zip <path-to-zip> --set dazzle-line
```

This creates:
- `UIElements/SVGicons/dazzle-line/*.svg` (source)
- `UIElements/SVGicons/dazzle-line/manifest.json` (source manifest)
- `UIElements/assets/icons/dazzle-line/*.svg` (public mirror)
- `UIElements/assets/icons/dazzle-line/manifest.json` (public manifest)

## Usage

### URL (HTML/CSS)

Use direct URL:

`/ui-elements/assets/icons/dazzle-line/<icon-name>.svg`

Example:

```html
<img src="/ui-elements/assets/icons/dazzle-line/alarm.svg" width="16" height="16" alt="" />
```

```css
.my-icon {
  width: 16px;
  height: 16px;
  background: currentColor;
  -webkit-mask: url('/ui-elements/assets/icons/dazzle-line/alarm.svg') center / contain no-repeat;
  mask: url('/ui-elements/assets/icons/dazzle-line/alarm.svg') center / contain no-repeat;
}
```

### JS helper

```js
import { svgIconImg, svgIconUrl } from '/ui-elements/assets/js/chrome.js';

const markup = svgIconImg('alarm', { setName: 'dazzle-line', size: 16 });
const url = svgIconUrl('alarm', 'dazzle-line');
```
