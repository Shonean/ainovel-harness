"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.CommentEliminator = void 0;
const CharacterSet_1 = require("./CharacterSet");
const StringParser_1 = require("./StringParser/StringParser");
/// <summary>
/// Pre-pass before main ink parser runs. It actually performs two main tasks:
///  - comment elimination to simplify the parse rules in the main parser
///  - Conversion of Windows line endings (\r\n) to the simpler Unix style (\n), so
///    we don't have to worry about them later.
/// </summary>
class CommentEliminator extends StringParser_1.StringParser {
    constructor() {
        super(...arguments);
        this._commentOrNewlineStartCharacter = new CharacterSet_1.CharacterSet("/\r\n");
        this._commentBlockEndCharacter = new CharacterSet_1.CharacterSet("*");
        this._newlineCharacters = new CharacterSet_1.CharacterSet("\n\r");
        this.Process = () => {
            // Make both comments and non-comments optional to handle trivial empty file case (or *only* comments)
            const stringList = this.Interleave(this.Optional(this.CommentsAndNewlines), this.Optional(this.MainInk));
            if (stringList !== null) {
                return stringList.join("");
            }
            else {
                return "";
            }
        };
        this.MainInk = () => this.ParseUntil(this.CommentsAndNewlines, this._commentOrNewlineStartCharacter, null);
        this.CommentsAndNewlines = () => {
            let newLines = this.Interleave(this.Optional(this.ParseNewline), this.Optional(this.ParseSingleComment));
            if (newLines !== null) {
                return newLines.join("");
            }
            return null;
        };
        // Valid comments always return either an empty string or pure newlines,
        // which we want to keep so that line numbers stay the same
        this.ParseSingleComment = () => this.OneOf([this.EndOfLineComment, this.BlockComment]);
        this.EndOfLineComment = () => {
            if (this.ParseString("//") === null) {
                return null;
            }
            this.ParseUntilCharactersFromCharSet(this._newlineCharacters);
            return "";
        };
        this.BlockComment = () => {
            if (this.ParseString("/*") === null) {
                return null;
            }
            const startLineIndex = this.lineIndex;
            const commentResult = this.ParseUntil(this.String("*/"), this._commentBlockEndCharacter, null);
            if (!this.endOfInput) {
                this.ParseString("*/");
            }
            // Count the number of lines that were inside the block, and replicate them as newlines
            // so that the line indexing still works from the original source
            if (commentResult != null) {
                return "\n".repeat(this.lineIndex - startLineIndex);
            }
            // No comment at all
            return null;
        };
    }
    PreProcessInputString(str) {
        return str;
    }
}
exports.CommentEliminator = CommentEliminator;
//# sourceMappingURL=CommentEliminator.js.map