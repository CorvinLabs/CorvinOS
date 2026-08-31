# MIT License — Contributor Plugins

**For use with CorvinOS Contributor Plugins**

---

## License Terms

MIT License

Copyright (c) [Year] [Your Name or Organization]

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.

---

## What This Means

### ✅ You Can:
- **Use** the plugin for any purpose (commercial, personal, educational)
- **Copy** the source code
- **Modify** the code and create derivative works
- **Distribute** the original or modified plugin
- **Sublicense** the plugin under any compatible license

### ❌ You Cannot:
- Hold the author liable for any issues or damages
- Use the author's name to endorse derivative works without permission

### ⚠️ Important Notices:
- **No Warranty:** The plugin is provided "as-is" with no guarantees
- **No Liability:** The author is not responsible for any problems
- **No Support:** The author is not obligated to provide technical support (but may choose to)

---

## How to Use This License

1. **Copy** the text above into a `LICENSE.txt` file in your plugin directory
2. **Replace** `[Year]` with the year of first publication
3. **Replace** `[Your Name or Organization]` with the copyright holder
4. **Include** in your plugin's root directory and in your `plugin.json` as `"license": "MIT"`

---

## Example plugin.json

```json
{
  "id": "plugin:contributor-memory-my_custom_memory",
  "type": "plugin",
  "name": "My Custom Memory Plugin",
  "version": "1.0.0",
  "author": "Jane Developer",
  "license": "MIT",
  "tier": "contributor",
  "category": "memory",
  "description": "A custom memory plugin for specialized use cases.",
  "distribution": {
    "supports_source": true,
    "supports_wheel": false,
    "source_url": "https://github.com/jane/my-plugin"
  },
  "boot_layer": "installed"
}
```

---

## Relationship to CorvinOS

CorvinOS itself is licensed under **Apache 2.0**. Contributor plugins may use any compatible license:

| Plugin Type | License | CLA Required? | SLA Guarantee? | Security Audit? |
|-------------|---------|---------------|----------------|-----------------|
| **Buildin** | Apache 2.0 | ✅ Yes (CLA) | ✅ 48h bugfix | ✅ Yes |
| **Contributor** | MIT (or compatible) | ❌ No | ❌ Community | ❌ No |

---

## Support & Questions

For CorvinOS contributor plugin licensing:
- **Forum:** CorvinOS Community Discussions
- **Email:** `plugins@corvinOS.dev`
- **Docs:** `docs/plugin-developer-guide.md`

---

## Full MIT License Text

For the complete, official MIT License text, see: https://opensource.org/licenses/MIT

---

**Last Updated:** 2026-08-31  
**License Version:** 1.0
