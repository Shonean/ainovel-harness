"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.Choice = void 0;
const Path_1 = require("./Path");
const NullException_1 = require("./NullException");
const Object_1 = require("./Object");
class Choice extends Object_1.InkObject {
    constructor() {
        super(...arguments);
        this.text = "";
        this.index = 0;
        this.threadAtGeneration = null;
        this.sourcePath = "";
        this.targetPath = null;
        this.isInvisibleDefault = false;
        this.tags = null;
        this.originalThreadIndex = 0;
    }
    get pathStringOnChoice() {
        if (this.targetPath === null)
            return (0, NullException_1.throwNullException)("Choice.targetPath");
        return this.targetPath.toString();
    }
    set pathStringOnChoice(value) {
        this.targetPath = new Path_1.Path(value);
    }
    Clone() {
        let copy = new Choice();
        copy.text = this.text;
        copy.sourcePath = this.sourcePath;
        copy.index = this.index;
        copy.targetPath = this.targetPath;
        copy.originalThreadIndex = this.originalThreadIndex;
        copy.isInvisibleDefault = this.isInvisibleDefault;
        if (this.threadAtGeneration !== null)
            copy.threadAtGeneration = this.threadAtGeneration.Copy();
        return copy;
    }
}
exports.Choice = Choice;
//# sourceMappingURL=Choice.js.map