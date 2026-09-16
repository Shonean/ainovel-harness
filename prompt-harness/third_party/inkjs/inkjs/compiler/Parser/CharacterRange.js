"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.CharacterRange = void 0;
const CharacterSet_1 = require("./CharacterSet");
/// <summary>
/// A class representing a character range. Allows for lazy-loading a corresponding <see cref="CharacterSet">character set</see>.
/// </summary>
class CharacterRange {
    constructor(_start, _end, excludes = []) {
        this._start = _start;
        this._end = _end;
        this._correspondingCharSet = new CharacterSet_1.CharacterSet();
        this._excludes = new Set();
        /// <summary>
        /// Returns a <see cref="CharacterSet">character set</see> instance corresponding to the character range
        /// represented by the current instance.
        /// </summary>
        /// <remarks>
        /// The internal character set is created once and cached in memory.
        /// </remarks>
        /// <returns>The char set.</returns>
        this.ToCharacterSet = () => {
            if (this._correspondingCharSet.set.size === 0) {
                for (let ii = this.start.charCodeAt(0), c; ii <= this.end.charCodeAt(0); ii += 1) {
                    c = String.fromCharCode(ii);
                    if (!this._excludes.has(c)) {
                        this._correspondingCharSet.AddCharacters(c);
                    }
                }
            }
            return this._correspondingCharSet;
        };
        if (excludes instanceof CharacterSet_1.CharacterSet) {
            this._excludes = excludes.set;
        }
        else {
            for (const item of excludes) {
                this._excludes.add(item);
            }
        }
    }
    get start() {
        return this._start;
    }
    get end() {
        return this._end;
    }
}
exports.CharacterRange = CharacterRange;
CharacterRange.Define = (start, end, excludes = []) => new CharacterRange(start, end, excludes);
//# sourceMappingURL=CharacterRange.js.map