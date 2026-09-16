"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.JsonFileHandler = void 0;
class JsonFileHandler {
    constructor(fileHierarchy) {
        this.fileHierarchy = fileHierarchy;
        this.ResolveInkFilename = (filename) => {
            if (Object.keys(this.fileHierarchy).includes(filename))
                return filename;
            throw new Error(`Cannot locate ${filename}. Are you trying a relative import ? This is not yet implemented.`);
        };
        this.LoadInkFileContents = (filename) => {
            if (Object.keys(this.fileHierarchy).includes(filename)) {
                return this.fileHierarchy[filename];
            }
            else {
                throw new Error(`Cannot open ${filename}.`);
            }
        };
    }
}
exports.JsonFileHandler = JsonFileHandler;
//# sourceMappingURL=JsonFileHandler.js.map