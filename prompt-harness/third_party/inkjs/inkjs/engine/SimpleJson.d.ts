export declare class SimpleJson {
    static TextToDictionary(text: string): Record<string, any>;
    static TextToArray(text: string): any[];
}
export declare namespace SimpleJson {
    class Reader {
        constructor(text: string);
        ToDictionary(): Record<string, any>;
        ToArray(): any[];
        private _rootObject;
    }
    class Writer {
        WriteObject(inner: (w: Writer) => void): void;
        WriteObjectStart(): void;
        WriteObjectEnd(): void;
        WriteProperty(name: any, innerOrContent: ((w: Writer) => void) | string | boolean | null): void;
        WriteIntProperty(name: any, content: number): void;
        WriteFloatProperty(name: any, content: number): void;
        WritePropertyStart(name: any): void;
        WritePropertyEnd(): void;
        WritePropertyNameStart(): void;
        WritePropertyNameEnd(): void;
        WritePropertyNameInner(str: string): void;
        WriteArrayStart(): void;
        WriteArrayEnd(): void;
        Write(value: number | string | boolean | null, escape?: boolean): void;
        WriteBool(value: boolean | null): void;
        WriteInt(value: number | null): void;
        WriteFloat(value: number | null): void;
        WriteNull(): void;
        WriteStringStart(): void;
        WriteStringEnd(): void;
        WriteStringInner(str: string | null, escape?: boolean): void;
        toString(): string;
        private StartNewObject;
        private get state();
        private get childCount();
        private get currentCollection();
        private get currentPropertyName();
        private IncrementChildCount;
        private Assert;
        private _addToCurrentObject;
        private _currentPropertyName;
        private _currentString;
        private _stateStack;
        private _collectionStack;
        private _propertyNameStack;
        private _jsonObject;
    }
    namespace Writer {
        enum State {
            None = 0,
            Object = 1,
            Array = 2,
            Property = 3,
            PropertyName = 4,
            String = 5
        }
        class StateElement {
            type: SimpleJson.Writer.State;
            childCount: number;
            constructor(type: SimpleJson.Writer.State);
        }
    }
}
