"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.StringParserElement = void 0;
class StringParserElement {
    constructor() {
        this.characterIndex = 0;
        this.characterInLineIndex = 0;
        this.lineIndex = 0;
        this.reportedErrorInScope = false;
        this.uniqueId = 0;
        this.customFlags = 0;
        this.CopyFrom = (fromElement) => {
            StringParserElement._uniqueIdCounter++;
            this.uniqueId = StringParserElement._uniqueIdCounter;
            this.characterIndex = fromElement.characterIndex;
            this.characterInLineIndex = fromElement.characterInLineIndex;
            this.lineIndex = fromElement.lineIndex;
            this.customFlags = fromElement.customFlags;
            this.reportedErrorInScope = false;
        };
        // Squash is used when succeeding from a rule,
        // so only the state information we wanted to carry forward is
        // retained. e.g. characterIndex and lineIndex are global,
        // however uniqueId is specific to the individual rule,
        // and likewise, custom flags are designed for the temporary
        // state of the individual rule too.
        this.SquashFrom = (fromElement) => {
            this.characterIndex = fromElement.characterIndex;
            this.characterInLineIndex = fromElement.characterInLineIndex;
            this.lineIndex = fromElement.lineIndex;
            this.reportedErrorInScope = fromElement.reportedErrorInScope;
            this.customFlags = fromElement.customFlags;
        };
    }
}
exports.StringParserElement = StringParserElement;
StringParserElement._uniqueIdCounter = 1000;
//# sourceMappingURL=StringParserElement.js.map