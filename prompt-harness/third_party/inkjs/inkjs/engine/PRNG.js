"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.PRNG = void 0;
// Taken from https://gist.github.com/blixt/f17b47c62508be59987b
// Ink uses a seedable PRNG of which there is none in native javascript.
class PRNG {
    constructor(seed) {
        this.seed = seed % 2147483647;
        if (this.seed <= 0)
            this.seed += 2147483646;
    }
    next() {
        return (this.seed = (this.seed * 48271) % 2147483647);
    }
    nextFloat() {
        return (this.next() - 1) / 2147483646;
    }
}
exports.PRNG = PRNG;
//# sourceMappingURL=PRNG.js.map