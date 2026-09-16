import { Path } from "./Path";
import { Container } from "./Container";
import { SearchResult } from "./SearchResult";
import { DebugMetadata } from "./DebugMetadata";
export declare class InkObject {
    parent: InkObject | null;
    get debugMetadata(): DebugMetadata | null;
    set debugMetadata(value: DebugMetadata | null);
    get ownDebugMetadata(): DebugMetadata | null;
    private _debugMetadata;
    DebugLineNumberOfPath(path: Path): number | null;
    get path(): Path;
    private _path;
    ResolvePath(path: Path | null): SearchResult;
    ConvertPathToRelative(globalPath: Path): Path;
    CompactPathString(otherPath: Path): string;
    get rootContentContainer(): Container | null;
    Copy(): InkObject;
    SetChild(obj: any, prop: any, value: any): void;
    Equals(obj: any): boolean;
}
