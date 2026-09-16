"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.JsonFileHandler = exports.PosixFileHandler = exports.CompilerOptions = exports.Compiler = exports.InkList = exports.Story = void 0;
var Story_1 = require("./engine/Story");
Object.defineProperty(exports, "Story", { enumerable: true, get: function () { return Story_1.Story; } });
Object.defineProperty(exports, "InkList", { enumerable: true, get: function () { return Story_1.InkList; } });
var Compiler_1 = require("./compiler/Compiler");
Object.defineProperty(exports, "Compiler", { enumerable: true, get: function () { return Compiler_1.Compiler; } });
Object.defineProperty(exports, "CompilerOptions", { enumerable: true, get: function () { return Compiler_1.CompilerOptions; } });
var PosixFileHandler_1 = require("./compiler/FileHandler/PosixFileHandler");
Object.defineProperty(exports, "PosixFileHandler", { enumerable: true, get: function () { return PosixFileHandler_1.PosixFileHandler; } });
var JsonFileHandler_1 = require("./compiler/FileHandler/JsonFileHandler");
Object.defineProperty(exports, "JsonFileHandler", { enumerable: true, get: function () { return JsonFileHandler_1.JsonFileHandler; } });
//# sourceMappingURL=ink.js.map