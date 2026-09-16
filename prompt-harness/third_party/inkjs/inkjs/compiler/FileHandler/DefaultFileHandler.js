"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.DefaultFileHandler = void 0;
// This class replaces upstream's DefaultFileHandler. It doesn't perform any
// resolution and warns the user about providing a proper file handler when
// INCLUDE statements are parsed. Since the JavaScript parser can be executed in
// different environments, we let the user decide which FileHandler is best for
// their use-case. See PosixFileHandler and JsonFileHandler.
class DefaultFileHandler {
    constructor(rootPath) {
        this.rootPath = rootPath;
        this.ResolveInkFilename = () => {
            throw Error("Can't resolve filename because no FileHandler was provided when instantiating the parser / compiler.");
        };
        this.LoadInkFileContents = () => {
            throw Error("Can't load ink content because no FileHandler was provided when instantiating the parser / compiler.");
        };
    }
}
exports.DefaultFileHandler = DefaultFileHandler;
//# sourceMappingURL=DefaultFileHandler.js.map