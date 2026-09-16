"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.LegacyTag = exports.Tag = void 0;
const Object_1 = require("./Object");
const ControlCommand_1 = require("../../../engine/ControlCommand");
class Tag extends Object_1.ParsedObject {
    constructor(isStart, inChoice = false) {
        super();
        this.GenerateRuntimeObject = () => {
            if (this.isStart) {
                return ControlCommand_1.ControlCommand.BeginTag();
            }
            else {
                return ControlCommand_1.ControlCommand.EndTag();
            }
        };
        this.toString = () => {
            if (this.isStart) {
                return "#StartTag";
            }
            else {
                return "#EndTag";
            }
        };
        this.isStart = isStart;
        this.inChoice = inChoice;
    }
    get typeName() {
        return "Tag";
    }
}
exports.Tag = Tag;
const Wrap_1 = require("./Wrap");
class LegacyTag extends Wrap_1.Wrap {
    constructor(tag) {
        super(tag);
    }
    get typeName() {
        return "Tag";
    }
}
exports.LegacyTag = LegacyTag;
//# sourceMappingURL=Tag.js.map