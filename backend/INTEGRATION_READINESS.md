# جاهزية تكامل قاعدة البيانات — Integration Preparation Part 4

**التاريخ:** 7 نوفمبر 2026

**الفرع:** `backend-dev1`

**المراجع:** `User story.docx`، `backend.docx`، `database final(1).docx`، عقود API وBackend الحالية.

## الحالة الحالية

هذا تحديث لحالة التكامل الحالية بعد تجهيز أساس Flask-SQLAlchemy، وعقود المصادقة والجلسات، ومنافذ المستودعات. لا يعني وجود العقد أن أي عملية MySQL تعمل: لا يوجد محول تخزين دائم مركب، ولا نماذج أعمال أو جداول أو migrations في Backend، ولا يزال إنشاء التطبيق بلا اتصال شبكي.

## ما هو جاهز

- `db.py` يعرّف امتدادي Flask-SQLAlchemy وFlask-Migrate، ومصنع التطبيق يربطهما. الإعداد يقبل `DATABASE_URL` أو قيم `DB_*`، ويستخدم SQLite داخل الذاكرة عند غياب إعدادات قاعدة البيانات. إنشاء التطبيق وحده لا يفتح اتصال MySQL.
- `auth_workflows.py` يعرّف `AuthRepository` و`AuthAccount`. سير التسجيل جاهز لاستدعاء إنشاء Owner والنشاط التجاري ذريًا دون فرع، مع تفرد البريد وتخزين hash فقط. سير الدخول يعيد استخدام التحقق من كلمة المرور، ويقرأ `users.access_version`.
- `session_auth.py` يوفّر `SessionStore` و`SessionManager` ومحمّل مستخدم موثوقًا يُستدعى عند كل طلب. الجلسة تحفظ `access_version_at_login`؛ عدم تطابقها مع `users.access_version` أو تعطيل الحساب يبطل الجلسة. يعرض سياق الجلسة الموثوق اللقطة نفسها لعمليات compare-and-swap. `delete` في واجهة الجلسة يعني إبطالًا فوريًا، ويمكن لمحول المخطط المعتمد تنفيذه بتعيين `auth_sessions.revoked_at` دون حذف الصف.
- محدد محاولات الدخول له عقد مستقل، ويعمل سير الدخول فقط إذا حُقنت محولات الحساب والجلسة ومحدد المحاولات المطلوبة. لا يوجد أي منها افتراضيًا.
- `permissions.py` و`isolation_rules.py` يوفّران الهوية الموثوقة، الأدوار والصلاحيات، وفحوص نطاق النشاط والفرع. يجب بناء الهوية من بيانات قاعدة البيانات؛ لا تُقبل هوية أو صلاحية أو `business_id` من الطلب.
- أُضيف `repository_contracts.py` لعقود `UserRepository` و`BranchRepository` وDTOs آمنة ومثبتات حقن صريحة. العقود تشترط نطاق النشاط المستمد من الجلسة، التحقق من صلاحية الفاعل، الذرية، ومعالجة موحدة للتعارض/الغياب/المنع/فشل الاستمرارية. تغييرات الدور أو الفرع أو كلمة المرور أو الحالة مطالبة بزيادة `access_version` وتسجيل التدقيق وإبطال الجلسات ذريًا.
- لا ينشئ `create_app()` مستودعًا افتراضيًا. المثبتات ترفض أي محول غير دائم ومشترك في وضع الإنتاج. المستودعات الوهمية محلية للاختبارات فقط.
- التحقق والمدخلات الأمنية موجودان في مسارات API. مسارات المستخدمين والفروع لا تزال غير موصولة بعقود المستودعات وتعيد `501` للعمليات الصحيحة. كما تبقى مسارات المصادقة على `501` عند غياب كل المحولات اللازمة. لم يتغير هذا السلوك.

## عقود البيانات وخريطة Backend إلى قاعدة البيانات

الأسماء أدناه مستندة إلى تعريفات `businesses` و`branches` و`users` و`auth_sessions` في `database final(1).docx`. خريطة الأعمدة توثيق للعقد فقط؛ لم تُنشأ نماذج أو جداول أو migrations.

| DTO أو العملية الحالية | أعمدة/جداول قاعدة البيانات | قواعد التحويل والنطاق |
|---|---|---|
| تسجيل Owner: `owner_name`, `business_name`, `email`, `password` | `businesses.name`; `users.full_name`, `email`, `password_hash`, `role`, `business_id`, `branch_id`, `created_by`, `access_version` | معاملة واحدة: إنشاء نشاط وOwner بدور `OWNER`، و`branch_id=NULL` و`created_by=NULL`، دون فرع تلقائي. `businesses.contact_email` ليس بريد دخول Owner. يبدأ `access_version` من القيمة الافتراضية المعتمدة (1). |
| دخول الحساب و`AuthAccount` | `users.id`, `business_id`, `branch_id`, `full_name`, `email`, `password_hash`, `role`, `is_active`, `access_version` | `AuthAccount.user_id` ← `users.id`؛ `AuthAccount.access_version` ← `users.access_version`. الدور المخزن `OWNER`/`BRANCH_MANAGER`/`EMPLOYEE` يتحول إلى `Role` في Backend. `principal` وعداد الفروع يُبنيان باستعلامات موثوقة ومقيدة بـ`business_id`. جدول Database لا يعرّف `is_deleted`؛ يلزم اعتماد تمثيل الحذف أو أن يصرّح المحول بأن `is_deleted=False` طالما الحذف الصلب غير مسموح. |
| `POST /api/branches`: `name`, `address`, `phone`, `notes` | `branches.business_id`, `name`, `address`, `phone`, `notes`, `is_active`, `created_by` | النشاط والمالك يؤخذان من الجلسة الموثوقة، لا من JSON. يُنشأ `is_active=TRUE`. التفرد للاسم داخل النشاط وغير حساس لحالة الأحرف. |
| استجابة/سجل الفرع: `branch_id`, الحقول القابلة للتحرير، `is_active` | `branches.id`, `business_id`, `name`, `address`, `phone`, `notes`, `is_active`, `created_by`, `created_at`, `updated_at` | كل قراءة أو تعديل أو تعطيل يقيد الاستعلام بـ`id` و`business_id` معًا. التعطيل تحديث `is_active=FALSE` ويحفظ السجل التاريخي؛ لا حذف. |
| `POST /api/users`: `name`, `email`, `role`, `branch_id`, `initial_password` | `users.full_name`, `email`, `role`, `branch_id`, `business_id`, `password_hash`, `created_by`, `is_active`, `access_version`; `user_account_audit.target_user_id`, `changed_by_user_id`, `action`, `old_values_json`, `new_values_json`, `created_at` | `name` ← `full_name`؛ `initial_password` يُهشّم ولا يُخزّن؛ `Branch Manager` ← `BRANCH_MANAGER` و`Employee` ← `EMPLOYEE`. يمنع إنشاء Owner عبر واجهة الموظفين. `business_id` و`created_by` من الجلسة، والفرع يجب أن ينتمي للنشاط نفسه. البريد فريد على مستوى المنصة. يبدأ `access_version` من الافتراضي 1 ويسجل الإنشاء في التدقيق ضمن المعاملة. |
| `PATCH /api/users/{id}/status`: `is_active` | `users.is_active`, `users.access_version`; أعمدة التدقيق أعلاه؛ `auth_sessions.revoked_at` | عند تغيير الحالة: زيادة `access_version`، وتسجيل `ACTIVATE` أو `DEACTIVATE`، وإبطال جلسات المستخدم ضمن معاملة واحدة. الجلسة القديمة تحمل نسختها في `access_version_at_login` فترفض عند إعادة التحميل. |
| تعيين دور/فرع مستخدم (مطلوب بالقصة، ولا يوجد له مسار API بعد) | `users.role`, `users.branch_id`, `users.access_version`; أعمدة التدقيق أعلاه؛ `auth_sessions.revoked_at` | تغيير ذري؛ تحقق من انتماء الفرع إلى النشاط؛ سجل `ROLE_CHANGE` أو `BRANCH_CHANGE`؛ زد `access_version` وأبطل الجلسات. لا يضاف مسار ضمن هذه المهمة. |
| `PATCH /api/users/me/password` و`AccountRecord` القائم | `users.password_hash`, `last_password_changed_at`, `access_version`; أعمدة التدقيق أعلاه؛ `auth_sessions.revoked_at` | `get_own_account` يعيد استخدام DTO `AccountRecord` وقواعد `account_settings_rules.py` للتحقق من كلمة المرور؛ لا يعرض hash. عملية الحفظ تقارن `access_version` الحالية بلقطة الجلسة ثم تعدل hash والتاريخ وتزيد النسخة وتكتب التدقيق وتبطل الجلسات ذريًا. لا تمرر كلمة مرور صريحة إلى المستودع. |
| `SessionRecord` و`SessionStore` | `auth_sessions.session_token_hash`, `business_id`, `user_id`, `access_version_at_login`, `created_at`, `expires_at`, `revoked_at`, `last_seen_at` | `token_digest` هو SHA-256 سداسي عشري بطول 64؛ يحوّل المحول 32 بايت إلى `BINARY(32)`. `access_version_at_login` يساوي لقطة `users.access_version` عند الدخول. يُشتق `business_id` من صف المستخدم في قاعدة البيانات، ويجب على `put` التحقق ذريًا أن المستخدم فعال وما زال يحمل النسخة نفسها. `get` يتجاهل الصفوف الملغاة و`delete` يضبط `revoked_at`. `last_seen_at` اختياري. |

### فروق تتطلب قرارًا قبل المحول الدائم

- Backend الحالي يولد hashes بطريقة Werkzeug scrypt، بينما مستند Database يذكر Argon2id أو bcrypt لـ`password_hash VARCHAR(255)`. يجب اعتماد خوارزمية التخزين بين Database والفريق قبل تشغيل التسجيل أو تغيير كلمة المرور على MySQL.
- `SessionRecord` يحتفظ بـ`csrf_token_digest` للتحقق من cookie/header. تعريف `auth_sessions` في مستند Database لا يتضمن عمودًا له. يجب أن يعتمد Database تخزينًا دائمًا متوافقًا أو يوافق على تصميم بديل قبل تفعيل الجلسات الدائمة؛ لم يُضف عمود أو مخطط هنا.
- توجد إشارة إلى 32 جدولًا في `backend.docx` وإلى 36 جدولًا إلزاميًا في `database final(1).docx`. على مالك Database تثبيت إصدار المخطط النهائي وعدد الجداول وDDL/migrations المطابقة قبل أي تطبيق.

## عقد الأخطاء

تعيد المستودعات أخطاء typed من `repository_contracts.py`: تعارض/تفرد إلى `RepositoryConflictError` (HTTP `409`)، غياب المورد أو كونه خارج النشاط إلى `RepositoryNotFoundError` (`404` دون كشف وجوده)، فشل صلاحية إلى `RepositoryAuthorizationError` (`403` بعد التحقق من الجلسة)، وفشل التخزين إلى `RepositoryPersistenceError` (`500` برسالة عامة). الجلسة الغائبة أو غير الصالحة تظل `401`، والمدخلات غير الصالحة تظل `400`. لا يلتقط أي رد تفاصيل SQL أو أسرارًا. غياب المحول لا يُحوّل إلى خطأ تخزين: يبقى `501` صريحًا.

## ما يجب على Database تسليمه

1. تثبيت إصدار مرجعي واحد للمخطط وعدد الجداول، وتسليم DDL أو migrations قابلة للتطبيق والتراجع، مع تعريفات الأعمدة والقيود والفهارس والعلاقات النهائية. لا تكفي ملفات Word لتشغيل ORM أو التحقق الآلي من المخطط.
2. تسليم MySQL 8.0.16+ للاختبار والإنتاج وفق InnoDB و`utf8mb4` والتوقيت UTC، مع مستخدمين وصلاحيات بأقل امتياز وإعدادات الاتصال عبر مخزن أسرار خارج Git.
3. تأكيد قيود تفرد البريد العالمي واسم الفرع داخل النشاط دون حساسية لحالة الأحرف، واستراتيجية المفاتيح المركبة/القيود التي تمنع ربط فرع أو سجل تابع بنشاط آخر.
4. توثيق معاملة تسجيل Owner، ومعاملة إنشاء/تعديل المستخدم، وآلية أقفال/تفرد آمنة تحت الطلبات المتزامنة. تأكيد أعمدة `access_version` و`auth_sessions.access_version_at_login`، وتدقيق الحساب، وإبطال الجلسات عبر `revoked_at`.
5. حسم سياسة hash كلمة المرور، ومكان hash الخاص بـCSRF، وتمثيل حالة `is_deleted` غير الموجودة في جدول `users`.
6. توفير إعدادات MySQL غير الإنتاجية وبيانات/حساب اختبار معتمدة تسمح باختبارات تكامل حقيقية. أي بيانات seed يجب أن تكون معتمدة ومحددة من Database، ولا تُنشأ هنا.

## الخطوات الدقيقة لتفعيل MySQL الحقيقي

1. **إغلاق قرارات المخطط:** استلام الإصدار والـDDL/migrations المعتمدة وإغلاق تعارض 32/36، ثم اعتماد قرارات hash وCSRF والحذف أعلاه.
2. **تجهيز البيئة:** يضبط مسؤول البيئة `DATABASE_URL` أو قيم `DB_*` في مخزن أسرار محلي/نشر، ويمنح Backend بيانات اعتماد MySQL للاختبار. لا تُحفظ القيم الحقيقية في Git.
3. **تطبيق مخطط Database:** يطبق Database migrations المعتمدة على قاعدة اختبار فارغة ويتحقق من القيود والفهارس والجداول؛ لا ينشئ Backend schema بديلًا يدويًا.
4. **بناء محولات الإنتاج:** يربط Backend `AuthRepository` و`UserRepository` و`BranchRepository` بجلسات `db.session` ومعاملات MySQL، ويضيف محول `SessionStore` ومحدد محاولات مشتركًا ذريًا. يبقى تكوين التطبيق بلا محولات تلقائيًا؛ تثبت المحولات صراحةً فقط في إعداد الإنتاج بعد أن تعلن `is_persistent=True` و`is_shared=True`.
5. **ربط الهوية والمسارات:** ينفذ محمل مستخدم يعيد قراءة الحالة والدور والنشاط والفرع و`access_version` عند كل طلب؛ يحمي مسارات الأعمال بـsession/CSRF وRBAC؛ ثم يوصل مسارات المستخدمين والفروع بالمنافذ. هذه المسارات تعيد `501` الآن، وتركيب المستودع وحده لا يفعّل عملياتها.
6. **التحقق على MySQL فعلي:** اختبر اتصالًا وقراءة وكتابة حقيقيين، ثم اختبارات تكامل للمعاملة الذرية، البريد/اسم الفرع المتزامن، عزل النشاط، صلاحية الفرع، التدقيق، زيادة النسخة وإبطال الجلسات، CSRF، والفشل/rollback. اختبارات SQLite أو fakes لا تثبت تكامل MySQL.
7. **التفعيل:** بعد نجاح الاختبارات ومراجعة Database، طبّق migrations عبر مسار الإصدار المعتمد، فعّل حقن المحولات الدائمة المشتركة وإعدادات الإنتاج الأمنية، ثم نفذ smoke test لـregister/login/logout وأول فرع وإنشاء/تعطيل مستخدم.

## الاختبارات وحالة MySQL

تستخدم اختبارات الوحدات مستودعات وهمية محقونة من ملفات الاختبار فقط. تختبر العقود ومثبتات الاعتماد ولا تتصل بـMySQL. لا يوجد حتى الآن دليل على اتصال MySQL حقيقي أو عملية CRUD دائمة ناجحة. نتيجة التشغيل الكامل بعد هذه التغييرات: `1055 passed in 14.87s` عبر `python -m pytest -q` من مجلد `backend`.
