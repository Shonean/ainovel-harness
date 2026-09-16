import { InkObject } from "./Object";
import { Container } from "./Container";
export declare class SearchResult {
    obj: InkObject | null;
    approximate: boolean;
    get correctObj(): InkObject | null;
    get container(): Container | null;
    copy(): SearchResult;
}
