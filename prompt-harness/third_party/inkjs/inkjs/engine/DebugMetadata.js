"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.DebugMetadata = void 0;
class DebugMetadata {
    constructor() {
        this.startLineNumber = 0;
        this.endLineNumber = 0;
        this.startCharacterNumber = 0;
        this.endCharacterNumber = 0;
        this.fileName = null;
        this.sourceName = null;
    }
    Merge(dm) {
        let newDebugMetadata = new DebugMetadata();
        newDebugMetadata.fileName = this.fileName;
        newDebugMetadata.sourceName = this.sourceName;
        if (this.startLineNumber < dm.startLineNumber) {
            newDebugMetadata.startLineNumber = this.startLineNumber;
            newDebugMetadata.startCharacterNumber = this.startCharacterNumber;
        }
        else if (this.startLineNumber > dm.startLineNumber) {
            newDebugMetadata.startLineNumber = dm.startLineNumber;
            newDebugMetadata.startCharacterNumber = dm.startCharacterNumber;
        }
        else {
            newDebugMetadata.startLineNumber = this.startLineNumber;
            newDebugMetadata.startCharacterNumber = Math.min(this.startCharacterNumber, dm.startCharacterNumber);
        }
        if (this.endLineNumber > dm.endLineNumber) {
            newDebugMetadata.endLineNumber = this.endLineNumber;
            newDebugMetadata.endCharacterNumber = this.endCharacterNumber;
        }
        else if (this.endLineNumber < dm.endLineNumber) {
            newDebugMetadata.endLineNumber = dm.endLineNumber;
            newDebugMetadata.endCharacterNumber = dm.endCharacterNumber;
        }
        else {
            newDebugMetadata.endLineNumber = this.endLineNumber;
            newDebugMetadata.endCharacterNumber = Math.max(this.endCharacterNumber, dm.endCharacterNumber);
        }
        return newDebugMetadata;
    }
    toString() {
        if (this.fileName !== null) {
            return `line ${this.startLineNumber} of ${this.fileName}"`;
        }
        else {
            return "line " + this.startLineNumber;
        }
    }
}
exports.DebugMetadata = DebugMetadata;
//# sourceMappingURL=DebugMetadata.js.map