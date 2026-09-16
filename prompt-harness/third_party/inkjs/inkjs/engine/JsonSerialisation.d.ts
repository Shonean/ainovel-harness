import { Container } from "./Container";
import { ListValue } from "./Value";
import { Choice } from "./Choice";
import { ListDefinitionsOrigin } from "./ListDefinitionsOrigin";
import { InkObject } from "./Object";
import { SimpleJson } from "./SimpleJson";
export declare class JsonSerialisation {
    static JArrayToRuntimeObjList(jArray: any[], skipLast?: boolean): InkObject[];
    static WriteDictionaryRuntimeObjs(writer: SimpleJson.Writer, dictionary: Map<string, InkObject>): void;
    static WriteListRuntimeObjs(writer: SimpleJson.Writer, list: InkObject[]): void;
    static WriteIntDictionary(writer: SimpleJson.Writer, dict: Map<string, number>): void;
    static WriteRuntimeObject(writer: SimpleJson.Writer, obj: InkObject): void;
    static JObjectToDictionaryRuntimeObjs(jObject: Record<string, any>): Map<string, InkObject>;
    static JObjectToIntDictionary(jObject: Record<string, any>): Map<string, number>;
    static JTokenToRuntimeObject(token: any): InkObject | null;
    static toJson<T>(me: T, removes?: (keyof T)[], space?: number): string;
    static WriteRuntimeContainer(writer: SimpleJson.Writer, container: Container | null, withoutName?: boolean): undefined;
    static JArrayToContainer(jArray: any[]): Container;
    static JObjectToChoice(jObj: Record<string, any>): Choice;
    static JArrayToTags(jObj: Record<string, any>): any;
    static WriteChoice(writer: SimpleJson.Writer, choice: Choice): void;
    static WriteChoiceTags(writer: SimpleJson.Writer, choice: Choice): void;
    static WriteInkList(writer: SimpleJson.Writer, listVal: ListValue): undefined;
    static ListDefinitionsToJToken(origin: ListDefinitionsOrigin): Record<string, any>;
    static JTokenToListDefinitions(obj: Record<string, any>): ListDefinitionsOrigin;
    private static _controlCommandNames;
}
