"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.CompilerOptions = void 0;
class CompilerOptions {
    constructor(sourceFilename = null, pluginNames = [], countAllVisits = false, errorHandler = null, fileHandler = null) {
        this.sourceFilename = sourceFilename;
        this.pluginNames = pluginNames;
        this.countAllVisits = countAllVisits;
        this.errorHandler = errorHandler;
        this.fileHandler = fileHandler;
    }
}
exports.CompilerOptions = CompilerOptions;
//# sourceMappingURL=CompilerOptions.js.map