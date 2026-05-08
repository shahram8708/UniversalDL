# Icon Conversion Guide

Use these methods to convert SVG icons to PNG for Chrome Web Store submission.

Method 1: Online converter
1. Open any trusted SVG to PNG converter website
2. Upload `icon16.svg`, `icon32.svg`, `icon48.svg`, `icon128.svg`
3. Export matching PNG sizes and keep the same filenames

Method 2: Inkscape CLI
```bash
inkscape icon16.svg -o icon16.png -w 16 -h 16
inkscape icon32.svg -o icon32.png -w 32 -h 32
inkscape icon48.svg -o icon48.png -w 48 -h 48
inkscape icon128.svg -o icon128.png -w 128 -h 128
```

Method 3: Node.js sharp library
```js
const sharp = require('sharp');

(async () => {
  await sharp('icon16.svg').resize(16, 16).png().toFile('icon16.png');
  await sharp('icon32.svg').resize(32, 32).png().toFile('icon32.png');
  await sharp('icon48.svg').resize(48, 48).png().toFile('icon48.png');
  await sharp('icon128.svg').resize(128, 128).png().toFile('icon128.png');
})();
```

Chrome extensions can use SVG icons directly in manifest.json in modern Chrome versions. PNG files are generally required for Chrome Web Store listing assets.
