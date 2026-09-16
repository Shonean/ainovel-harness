"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.Compiler = exports.Story = exports.InkList = exports.JsonFileHandler = exports.StatementLevel = exports.InkParser = exports.CompilerOptions = void 0;
const CompilerOptions_1 = require("./CompilerOptions");
const DebugSourceRange_1 = require("./DebugSourceRange");
const ErrorType_1 = require("./Parser/ErrorType");
const InkParser_1 = require("./Parser/InkParser");
const Value_1 = require("../engine/Value");
const TypeAssertion_1 = require("../engine/TypeAssertion");
const Stats_1 = require("./Stats");
var CompilerOptions_2 = require("./CompilerOptions");
Object.defineProperty(exports, "CompilerOptions", { enumerable: true, get: function () { return CompilerOptions_2.CompilerOptions; } });
var InkParser_2 = require("./Parser/InkParser");
Object.defineProperty(exports, "InkParser", { enumerable: true, get: function () { return InkParser_2.InkParser; } });
var StatementLevel_1 = require("./Parser/StatementLevel");
Object.defineProperty(exports, "StatementLevel", { enumerable: true, get: function () { return StatementLevel_1.StatementLevel; } });
var JsonFileHandler_1 = require("./FileHandler/JsonFileHandler");
Object.defineProperty(exports, "JsonFileHandler", { enumerable: true, get: function () { return JsonFileHandler_1.JsonFileHandler; } });
var Story_1 = require("../engine/Story");
Object.defineProperty(exports, "InkList", { enumerable: true, get: function () { return Story_1.InkList; } });
Object.defineProperty(exports, "Story", { enumerable: true, get: function () { return Story_1.Story; } });
class Compiler {
    get errors() {
        return this._errors;
    }
    get warnings() {
        return this._warnings;
    }
    get authorMessages() {
        return this._authorMessages;
    }
    get inputString() {
        return this._inputString;
    }
    get options() {
        return this._options;
    }
    get parsedStory() {
        if (!this._parsedStory) {
            throw new Error();
        }
        return this._parsedStory;
    }
    get runtimeStory() {
        if (!this._runtimeStory) {
            throw new Error("Compilation failed.");
        }
        return this._runtimeStory;
    }
    get parser() {
        if (!this._parser) {
            throw new Error();
        }
        return this._parser;
    }
    get debugSourceRanges() {
        return this._debugSourceRanges;
    }
    constructor(inkSource, options = null) {
        this._errors = [];
        this._warnings = [];
        this._authorMessages = [];
        this._parsedStory = null;
        this._runtimeStory = null;
        this._parser = null;
        this._debugSourceRanges = [];
        this.Compile = () => {
            this._parser = new InkParser_1.InkParser(this.inputString, this.options.sourceFilename || null, this.OnError, null, this.options.fileHandler);
            this._parsedStory = this.parser.ParseStory();
            if (this.errors.length === 0) {
                this.parsedStory.countAllVisits = this.options.countAllVisits;
                this._runtimeStory = this.parsedStory.ExportRuntime(this.OnError);
            }
            else {
                this._runtimeStory = null;
            }
            return this.runtimeStory;
        };
        this.RetrieveDebugSourceForLatestContent = () => {
            var _a;
            for (const outputObj of this.runtimeStory.state.outputStream) {
                const textContent = (0, TypeAssertion_1.asOrNull)(outputObj, Value_1.StringValue);
                if (textContent !== null) {
                    const range = new DebugSourceRange_1.DebugSourceRange(((_a = textContent.value) === null || _a === void 0 ? void 0 : _a.length) || 0, textContent.debugMetadata, textContent.value || "unknown");
                    this.debugSourceRanges.push(range);
                }
            }
        };
        this.GenerateStats = () => {
            if (this._parsedStory === null) {
                return null;
            }
            return (0, Stats_1.GenerateStoryStats)(this._parsedStory);
        };
        this.DebugMetadataForContentAtOffset = (offset) => {
            let currOffset = 0;
            let lastValidMetadata = null;
            for (const range of this.debugSourceRanges) {
                if (range.debugMetadata !== null) {
                    lastValidMetadata = range.debugMetadata;
                }
                if (offset >= currOffset && offset < currOffset + range.length) {
                    return lastValidMetadata;
                }
                currOffset += range.length;
            }
            return null;
        };
        this.OnError = (message, errorType) => {
            switch (errorType) {
                case ErrorType_1.ErrorType.Author:
                    this._authorMessages.push(message);
                    break;
                case ErrorType_1.ErrorType.Warning:
                    this._warnings.push(message);
                    break;
                case ErrorType_1.ErrorType.Error:
                    this._errors.push(message);
                    break;
            }
            if (this.options.errorHandler !== null) {
                this.options.errorHandler(message, errorType);
            }
        };
        this._inputString = inkSource;
        this._options = options || new CompilerOptions_1.CompilerOptions();
    }
}
exports.Compiler = Compiler;
//# sourceMappingURL=Compiler.js.map