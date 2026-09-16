"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.IncludedFile = void 0;
const Object_1 = require("./Object");
class IncludedFile extends Object_1.ParsedObject {
    constructor(includedStory) {
        super();
        this.includedStory = includedStory;
        this.GenerateRuntimeObject = () => {
            // Left to the main story to process
            return null;
        };
    }
    get typeName() {
        return "IncludedFile";
    }
}
exports.IncludedFile = IncludedFile;
//# sourceMappingURL=IncludedFile.js.map