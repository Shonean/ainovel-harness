"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.Gather = void 0;
const Container_1 = require("../../../../engine/Container");
const Object_1 = require("../Object");
const SymbolType_1 = require("../SymbolType");
class Gather extends Object_1.ParsedObject {
    get name() {
        var _a;
        return ((_a = this.identifier) === null || _a === void 0 ? void 0 : _a.name) || null;
    }
    get runtimeContainer() {
        return this.runtimeObject;
    }
    constructor(identifier, indentationDepth) {
        super();
        this.indentationDepth = indentationDepth;
        this.GenerateRuntimeObject = () => {
            const container = new Container_1.Container();
            container.name = this.name;
            if (this.story.countAllVisits) {
                container.visitsShouldBeCounted = true;
            }
            container.countingAtStartOnly = true;
            // A gather can have null content, e.g. it's just purely a line with "-"
            if (this.content) {
                for (const c of this.content) {
                    container.AddContent(c.runtimeObject);
                }
            }
            return container;
        };
        this.toString = () => { var _a, _b; return `- ${((_a = this.identifier) === null || _a === void 0 ? void 0 : _a.name) ? "(" + ((_b = this.identifier) === null || _b === void 0 ? void 0 : _b.name) + ")" : "gather"}`; };
        if (identifier)
            this.identifier = identifier;
    }
    get typeName() {
        return "Gather";
    }
    ResolveReferences(context) {
        super.ResolveReferences(context);
        if (this.identifier && (this.identifier.name || "").length > 0) {
            context.CheckForNamingCollisions(this, this.identifier, SymbolType_1.SymbolType.SubFlowAndWeave);
        }
    }
}
exports.Gather = Gather;
//# sourceMappingURL=Gather.js.map