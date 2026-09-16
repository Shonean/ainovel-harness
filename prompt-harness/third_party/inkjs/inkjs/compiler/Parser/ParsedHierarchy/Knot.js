"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.Knot = void 0;
const FlowBase_1 = require("./Flow/FlowBase");
const FlowLevel_1 = require("./Flow/FlowLevel");
class Knot extends FlowBase_1.FlowBase {
    get flowLevel() {
        return FlowLevel_1.FlowLevel.Knot;
    }
    constructor(name, topLevelObjects, args, isFunction) {
        super(name, topLevelObjects, args, isFunction);
    }
    get typeName() {
        return this.isFunction ? "Function" : "Knot";
    }
    ResolveReferences(context) {
        super.ResolveReferences(context);
        let parentStory = this.story;
        // Enforce rule that stitches must not have the same
        // name as any knots that exist in the story
        for (const stitchName in this.subFlowsByName) {
            const knotWithStitchName = parentStory.ContentWithNameAtLevel(stitchName, FlowLevel_1.FlowLevel.Knot, false);
            if (knotWithStitchName) {
                const stitch = this.subFlowsByName.get(stitchName);
                const errorMsg = `Stitch '${stitch ? stitch.name : "NO STITCH FOUND"}' has the same name as a knot (on ${knotWithStitchName.debugMetadata})`;
                this.Error(errorMsg, stitch);
            }
        }
    }
}
exports.Knot = Knot;
//# sourceMappingURL=Knot.js.map