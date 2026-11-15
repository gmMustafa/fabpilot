/**
 * `cn`: join class names and resolve Tailwind conflicts (the later class wins).
 *
 * Configured, not the package's default: our theme adds font sizes (text-heading, text-body,
 * text-button; globals.css). Unregistered, `text-button` LOOKS like a text colour, so the
 * merge dropped the real colour before it: `cn("text-primary-foreground", "text-button")` lost
 * the white of every primary button. Registered as font sizes, they no longer conflict with
 * colours (and still replace text-sm etc.). Add new theme sizes here too.
 */
import { createCn } from "cn/config";

export const THEME_FONT_SIZES = ["heading", "body", "button"];

export const cn = createCn({
  extend: { classGroups: { "font-size": [{ text: THEME_FONT_SIZES }] } },
});
