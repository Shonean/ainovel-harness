"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.ContentList = void 0;
const Container_1 = require("../../../engine/Container");
const Object_1 = require("./Object");
const Text_1 = require("./Text");
const TypeAssertion_1 = require("../../../engine/TypeAssertion");
class ContentList extends Object_1.ParsedObject {
    get runtimeContainer() {
        return this.runtimeObject;
    }
    constructor(objects, ...moreObjects) {
        super();
        this.dontFlatten = false;
        this.TrimTrailingWhitespace = () => {
            for (let ii = this.content.length - 1; ii >= 0; --ii) {
                const text = (0, TypeAssertion_1.asOrNull)(this.content[ii], Text_1.Text);
                if (text === null) {
                    break;
                }
                text.text = text.text.replace(new RegExp(/[ \t]/g), "");
                if (text.text.length === 0) {
                    this.content.splice(ii, 1);
                }
                else {
                    break;
                }
            }
        };
        this.GenerateRuntimeObject = () => {
            const container = new Container_1.Container();
            if (this.content !== null) {
                for (const obj of this.content) {
                    const contentObjRuntime = obj.runtimeObject;
                    // Some objects (e.g. author warnings) don't generate runtime objects
                    if (contentObjRuntime) {
                        container.AddContent(contentObjRuntime);
                    }
                }
            }
            if (this.dontFlatten) {
                this.story.DontFlattenContainer(container);
            }
            return container;
        };
        this.toString = () => `ContentList(${this.content.join(", ")})`;
        if (objects) {
            this.AddContent(objects);
        }
        if (moreObjects) {
            this.AddContent(moreObjects);
        }
    }
    get typeName() {
        return "ContentList";
    }
}
exports.ContentList = ContentList;
//# sourceMappingURL=ContentList.js.map