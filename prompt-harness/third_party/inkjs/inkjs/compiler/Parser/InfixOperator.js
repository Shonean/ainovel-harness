"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.InfixOperator = void 0;
class InfixOperator {
    constructor(type, precedence, requireWhitespace) {
        this.type = type;
        this.precedence = precedence;
        this.requireWhitespace = requireWhitespace;
        this.toString = () => this.type;
    }
}
exports.InfixOperator = InfixOperator;
//# sourceMappingURL=InfixOperator.js.map