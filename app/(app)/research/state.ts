export type ResearchArticleCreateFormValues = {
  url: string;
  forceProjectId: string;
  note: string;
};

export type ResearchArticleCreateActionState = {
  ok: boolean;
  message: string | null;
  articleId: string | null;
  scrapeJobId: string | null;
  existingArticle: boolean;
  form: ResearchArticleCreateFormValues;
};

export const initialResearchArticleCreateState: ResearchArticleCreateActionState = {
  ok: false,
  message: null,
  articleId: null,
  scrapeJobId: null,
  existingArticle: false,
  form: {
    url: "",
    forceProjectId: "",
    note: ""
  }
};
