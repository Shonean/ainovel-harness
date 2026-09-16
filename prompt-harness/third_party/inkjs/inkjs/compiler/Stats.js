"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.GenerateStoryStats = void 0;
const Choice_1 = require("./Parser/ParsedHierarchy/Choice");
const Divert_1 = require("./Parser/ParsedHierarchy/Divert/Divert");
const Gather_1 = require("./Parser/ParsedHierarchy/Gather/Gather");
const Knot_1 = require("./Parser/ParsedHierarchy/Knot");
const Stitch_1 = require("./Parser/ParsedHierarchy/Stitch");
const Text_1 = require("./Parser/ParsedHierarchy/Text");
function GenerateStoryStats(story) {
    let allText = story.FindAll(Text_1.Text)();
    let words = 0;
    for (const text of allText) {
        let wordsInThisStr = 0;
        let wasWhiteSpace = true;
        for (const c of text.text) {
            if (c == " " || c == "\t" || c == "\n" || c == "\r") {
                wasWhiteSpace = true;
            }
            else if (wasWhiteSpace) {
                wordsInThisStr++;
                wasWhiteSpace = false;
            }
        }
        words += wordsInThisStr;
    }
    const knots = story.FindAll(Knot_1.Knot)();
    const stitches = story.FindAll(Stitch_1.Stitch)();
    const choices = story.FindAll(Choice_1.Choice)();
    const gathers = story.FindAll(Gather_1.Gather)((g) => g.debugMetadata != null);
    const diverts = story.FindAll(Divert_1.Divert)();
    return {
        words,
        knots: knots.length,
        functions: knots.filter((k) => k.isFunction).length,
        stitches: stitches.length,
        gathers: gathers.length,
        diverts: diverts.length - 1,
        choices: choices.length,
    };
}
exports.GenerateStoryStats = GenerateStoryStats;
//# sourceMappingURL=Stats.js.map