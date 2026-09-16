"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.PosixFileHandler = void 0;
const path = require("path");
const fs = require("fs");
// This class replaces upstream's DefaultFileHandler.
class PosixFileHandler {
    constructor(rootPath = "") {
        this.rootPath = rootPath;
        this.ResolveInkFilename = (filename) => {
            return path.resolve(process.cwd(), this.rootPath, filename);
        };
        this.LoadInkFileContents = (filename) => {
            return fs.readFileSync(filename, "utf-8");
        };
    }
}
exports.PosixFileHandler = PosixFileHandler;
//# sourceMappingURL=PosixFileHandler.js.map