"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.Stitch = void 0;
const FlowBase_1 = require("./Flow/FlowBase");
const FlowLevel_1 = require("./Flow/FlowLevel");
class Stitch extends FlowBase_1.FlowBase {
    get flowLevel() {
        return FlowLevel_1.FlowLevel.Stitch;
    }
    constructor(name, topLevelObjects, args, isFunction) {
        super(name, topLevelObjects, args, isFunction);
        // Fixes TS issue with not being able to access the prototype via `super` in functions
        // attached to the class as properties.
        this.baseToString = this.toString;
        this.toString = () => {
            return `${this.parent !== null ? this.parent + " > " : ""}${this.baseToString()}`;
        };
    }
    get typeName() {
        return "Stitch";
    }
}
exports.Stitch = Stitch;
//# sourceMappingURL=Stitch.js.map