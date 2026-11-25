# V0.3 高频辐照度来源复核

本次仅查权威元数据和条款，不接入或重新分发高频数据，不声称校准完成。NASA历史输入与灰色层模型沿用ADR0005。

2026-11-25实际访问DataCite出版者元数据、PANGAEA数据页JSON-LD和AWI BSRN条款。Tateno 2026-09（10.1594/PANGAEA.998010）明确rightsIdentifier=bsrn-1.0，rights=Baseline Surface Radiation Network License 1.0；Tateno集合10.1594/PANGAEA.968727同样如此。条款允许免费科研，要求引用具体站点/月份并致谢WRMC，限制向第三方传递，另有增值发布例外及禁止直接售卖条件。因此不能以“免费下载”推断任意再分发，也不能误写为CC BY4.0。后续校准可评估满足条款的本地研究使用，正式数据集若包含输入子集需另核对发布方式；当前未获取辐照数据矩阵，采样间隔尚未实测核对，不能作为秒级校准验收证据。

- https://bsrn.awi.de/data/conditions-of-data-release/
- https://api.datacite.org/dois/10.1594/pangaea.998010
- https://doi.pangaea.de/10.1594/PANGAEA.998010?format=metadata_jsonld

快照仅本地data/v03-source-review-20261125：条款HTML SHA256 2bebcebd771c375f0deb4f00129c9f8718b6a628a868968387f1699c8db8454b；DataCite JSON 2aa8bba5a4971b52309aca6d032486c88317f901ad83ff12b385bdd1bf9f4a0c；PANGAEA JSON-LD 26ce86e5d018328471ea521606bcd9420f38c680a0d4b5d6a465efa3f583c3bb。错误的description-of-data路径返回404，未作来源。Oahu原站禁再分发与门户CC标记的冲突仍未解决，不接入。
