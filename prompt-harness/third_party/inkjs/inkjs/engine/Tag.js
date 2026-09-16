"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.Tag = void 0;
const Object_1 = require("./Object");
// New version of tags is dynamic - it constructs the tags
// at runtime based on BeginTag and EndTag control commands.
// Plain text that's in the output stream is turned into tags
// when you do story.currentTags.
// The only place this is used is when flattening tags down
// to string in advance, during dynamic string generation if
// there's a tag embedded in it. See how ControlCommand.EndString
// is implemented in Story.cs for more details + comment
class Tag extends Object_1.InkObject {
    constructor(tagText) {
        super();
        this.text = tagText.toString() || "";
    }
    toString() {
        return "# " + this.text;
    }
}
exports.Tag = Tag;
//# sourceMappingURL=Tag.js.map